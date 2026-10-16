from __future__ import annotations

import json
import math
import threading
import time
from collections import defaultdict, deque
from datetime import datetime, timezone
from functools import lru_cache
from hashlib import sha256
from typing import Iterable

from redis import Redis
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import Settings, get_settings
from app.models import Ticket, ModelUsage, WorkflowRun


def percentile(values: Iterable[float], percent: float) -> float:
    ordered = sorted(float(value) for value in values)
    if not ordered:
        return 0.0
    index = max(0, min(len(ordered) - 1, math.ceil(percent / 100 * len(ordered)) - 1))
    return round(ordered[index], 3)


def latency_summary(values: Iterable[float]) -> dict[str, float | int]:
    samples = list(values)
    return {
        "samples": len(samples),
        "p50_ms": percentile(samples, 50),
        "p95_ms": percentile(samples, 95),
        "average_ms": round(sum(samples) / len(samples), 3) if samples else 0.0,
        "max_ms": round(max(samples), 3) if samples else 0.0,
    }


class PerformanceRegistry:
    """Bounded performance telemetry with Redis sharing and an in-process fallback."""

    def __init__(self, settings: Settings | None = None) -> None:
        self.settings = settings or get_settings()
        self._samples: deque[dict] = deque(maxlen=self.settings.performance_sample_window)
        self._counters: defaultdict[str, int] = defaultdict(int)
        self._rate_windows: dict[tuple[str, int], int] = {}
        self._lock = threading.Lock()
        self._redis: Redis | None = None

    def _redis_client(self) -> Redis | None:
        if not self.settings.async_tasks_enabled:
            return None
        if self._redis is None:
            self._redis = Redis.from_url(
                self.settings.redis_url,
                decode_responses=True,
                socket_connect_timeout=0.25,
                socket_timeout=0.5,
            )
        return self._redis

    def record_http(self, route: str, method: str, status_code: int, duration_ms: float) -> None:
        sample = {
            "route": route,
            "method": method,
            "status_code": status_code,
            "duration_ms": round(duration_ms, 3),
            "timestamp": time.time(),
        }
        client = self._redis_client()
        if client is not None:
            try:
                pipe = client.pipeline()
                pipe.lpush("resolveai:performance:http", json.dumps(sample))
                pipe.ltrim("resolveai:performance:http", 0, self.settings.performance_sample_window - 1)
                pipe.execute()
                return
            except Exception:
                pass
        with self._lock:
            self._samples.append(sample)

    def record_cache(self, hit: bool) -> None:
        name = "retrieval_cache_hits" if hit else "retrieval_cache_misses"
        client = self._redis_client()
        if client is not None:
            try:
                client.hincrby("resolveai:performance:counters", name, 1)
                return
            except Exception:
                pass
        with self._lock:
            self._counters[name] += 1

    def record_guard_rejection(self, kind: str) -> None:
        name = f"guard_{kind}"
        client = self._redis_client()
        if client is not None:
            try:
                client.hincrby("resolveai:performance:counters", name, 1)
                return
            except Exception:
                pass
        with self._lock:
            self._counters[name] += 1

    def check_rate_limit(self, client_identity: str) -> tuple[bool, int, int]:
        limit = self.settings.rate_limit_requests_per_minute
        current_minute = int(time.time() // 60)
        retry_after = 60 - int(time.time() % 60)
        identity_hash = sha256(client_identity.encode("utf-8")).hexdigest()[:20]
        key = f"resolveai:rate:{current_minute}:{identity_hash}"
        client = self._redis_client()
        if client is not None:
            try:
                pipe = client.pipeline()
                pipe.incr(key)
                pipe.expire(key, 90)
                count, _ = pipe.execute()
                return int(count) <= limit, max(0, limit - int(count)), retry_after
            except Exception:
                pass
        local_key = (identity_hash, current_minute)
        with self._lock:
            count = self._rate_windows.get(local_key, 0) + 1
            self._rate_windows = {
                item_key: value
                for item_key, value in self._rate_windows.items()
                if item_key[1] >= current_minute - 1
            }
            self._rate_windows[local_key] = count
        return count <= limit, max(0, limit - count), retry_after

    def snapshot(self) -> dict:
        samples: list[dict]
        counters: dict[str, int]
        client = self._redis_client()
        if client is not None:
            try:
                samples = [json.loads(item) for item in client.lrange("resolveai:performance:http", 0, -1)]
                counters = {key: int(value) for key, value in client.hgetall("resolveai:performance:counters").items()}
            except Exception:
                samples, counters = self._local_snapshot()
        else:
            samples, counters = self._local_snapshot()

        durations = [float(item["duration_ms"]) for item in samples]
        route_samples: defaultdict[str, list[float]] = defaultdict(list)
        error_count = 0
        for item in samples:
            route_key = f"{item['method']} {item['route']}"
            route_samples[route_key].append(float(item["duration_ms"]))
            error_count += int(item["status_code"] >= 500)
        hits = counters.get("retrieval_cache_hits", 0)
        misses = counters.get("retrieval_cache_misses", 0)
        total_cache = hits + misses
        return {
            "http": {
                **latency_summary(durations),
                "server_errors": error_count,
                "by_route": {
                    route: latency_summary(route_durations)
                    for route, route_durations in sorted(route_samples.items())
                },
            },
            "retrieval_cache": {
                "hits": hits,
                "misses": misses,
                "hit_rate": round(hits / total_cache, 4) if total_cache else 0.0,
            },
            "guards": {
                "rate_limit_rejections": counters.get("guard_rate_limit", 0),
                "concurrency_rejections": counters.get("guard_concurrency", 0),
            },
        }

    def _local_snapshot(self) -> tuple[list[dict], dict[str, int]]:
        with self._lock:
            return list(self._samples), dict(self._counters)


@lru_cache
def get_performance_registry() -> PerformanceRegistry:
    return PerformanceRegistry()


def _has_complete_cost(item: dict) -> bool:
    cost = item.get("estimated_cost_usd")
    return (isinstance(cost, (int, float)) and not isinstance(cost, bool)
            and math.isfinite(cost) and cost >= 0
            and item.get("cost_complete") is not False
            and item.get("usage_available") is not False)


def build_performance_report(session: Session) -> dict:
    settings = get_settings()
    from app.services.auth import ticket_scope
    tickets = list(session.scalars(select(Ticket).where(ticket_scope())).all())
    ids = [ticket.id for ticket in tickets]
    ledger = list(session.scalars(select(ModelUsage).where(ModelUsage.ticket_id.in_(ids))).all())
    runs = list(session.scalars(select(WorkflowRun).where(WorkflowRun.ticket_id.in_(ids))).all())
    ledger_tickets = {row.ticket_id for row in ledger}
    workflow_durations: list[float] = []
    model_rows: list[dict] = [row.usage for row in ledger]
    legacy_trace_calls = 0
    for ticket in tickets:
        assessment = ticket.assessment or {}
        duration = (assessment.get("observability") or {}).get("workflow_duration_ms")
        if not runs and isinstance(duration, (int, float)):
            workflow_durations.append(float(duration))
        for item in assessment.get("model_trace") or []:
            if ticket.id not in ledger_tickets and not item.get("skipped"):
                # Retain old snapshots for diagnostics, never mix them into ledger averages.
                legacy_trace_calls += 1

    by_model: defaultdict[str, dict[str, float | int]] = defaultdict(
        lambda: {"calls": 0, "input_tokens": 0, "output_tokens": 0, "total_tokens": 0, "estimated_cost_usd": 0.0}
    )
    for item in model_rows:
        bucket = by_model[str(item.get("model") or "unknown")]
        bucket["calls"] += 1
        for name in ("input_tokens", "output_tokens", "total_tokens"):
            bucket[name] += int(item.get(name) or 0)
        if _has_complete_cost(item):
            bucket["estimated_cost_usd"] += float(item["estimated_cost_usd"])

    total_calls = len(model_rows)
    total_tokens = sum(int(item.get("total_tokens") or 0) for item in model_rows)
    cost_rows = [item for item in model_rows if _has_complete_cost(item)]
    total_cost = sum(float(item["estimated_cost_usd"]) for item in cost_rows)
    unknown = total_calls - len(cost_rows)
    customer_cycle = []
    for ticket in tickets:
        confirmation = (ticket.assessment or {}).get("customer_confirmation") or {}
        if ticket.status != "resolved" or not confirmation.get("resolved") or not confirmation.get("confirmed_at"):
            continue
        try:
            from app.services.metering import utc
            duration = (utc(datetime.fromisoformat(confirmation["confirmed_at"])) - utc(ticket.created_at)).total_seconds() * 1000
            if duration >= 0:
                customer_cycle.append(duration)
        except (ValueError, TypeError):
            continue
    runtime = get_performance_registry().snapshot()
    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        **runtime,
        "workflow": {
            **latency_summary([row.end_to_end_ms for row in runs if row.end_to_end_ms is not None] or workflow_durations),
            "queue": latency_summary([row.queue_ms for row in runs if row.queue_ms is not None]),
            "processing": latency_summary([row.processing_ms for row in runs if row.processing_ms is not None]),
            "failed_runs": sum(row.status == "failed" for row in runs),
            "waiting_approval_runs": sum(row.status == "waiting_approval" for row in runs),
            "timing_scope": "submission-to-worker-completion including queue, excluding human wait and customer resolution",
            "durable_runs": len(runs),
            "customer_resolution_cycle": {**latency_summary(customer_cycle), "scope": "ticket-created-to-customer-confirmed; includes human/customer wait; only explicitly resolved samples"},
        },
        "models": {
            "calls": total_calls,
            "total_tokens": total_tokens,
            "average_tokens_per_call": round(total_tokens / total_calls, 2) if total_calls else 0.0,
            "estimated_cost_usd": None if unknown else round(total_cost, 8),
            "known_estimated_cost_usd": round(total_cost, 8),
            "average_cost_per_call_usd": None if unknown or not total_calls else round(total_cost / total_calls, 8),
            "complete_cost_calls": len(cost_rows),
            "recorded_average_cost_per_call_usd": round(total_cost / len(cost_rows), 8) if cost_rows else None,
            "recorded_cost_scope": "complete-model-usage-ledger-only",
            "unknown_cost_calls": unknown,
            "cost_complete": not unknown,
            "cost_is_invoice": False,
            "cost_lower_usd": None if unknown else round(sum(item.get("cost_lower_usd") or 0 for item in model_rows), 8),
            "cost_upper_usd": None if unknown else round(sum(item.get("cost_upper_usd") or 0 for item in model_rows), 8),
            "failed_attempts": sum(item.get("status") == "error" for item in model_rows),
            "ledger_calls": len(ledger),
            "legacy_trace_calls": legacy_trace_calls,
            "real_api_attempts": sum(row.usage.get("provider") in {"deepseek", "openai"} for row in ledger),
            "real_api_tokens": sum(int(row.usage.get("total_tokens") or 0) for row in ledger
                                   if row.usage.get("provider") in {"deepseek", "openai"} and row.usage.get("usage_available")),
            "demo_calls": sum(row.usage.get("provider") == "demo" for row in ledger),
            "by_model": [
                {"model": model, **values, "estimated_cost_usd": round(float(values["estimated_cost_usd"]), 8)
                 if all(_has_complete_cost(row) for row in model_rows if (row.get("model") or "unknown") == model) else None}
                for model, values in sorted(by_model.items())
            ],
            "routing": {
                "primary_model": settings.model_for_stage("resolution"),
                "economy_model": settings.model_for_stage("triage"),
                "economy_stages": sorted(settings.noncritical_stage_set),
            },
        },
        "limits": {
            "rate_limit_enabled": settings.rate_limit_enabled,
            "requests_per_minute": settings.rate_limit_requests_per_minute,
            "max_concurrent_requests": settings.max_concurrent_requests,
            "max_context_chars": settings.model_max_context_chars,
            "max_conversation_messages": settings.agent_max_conversation_messages,
            "rag_max_documents": settings.rag_max_documents,
        },
    }
