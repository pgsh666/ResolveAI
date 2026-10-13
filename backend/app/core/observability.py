from __future__ import annotations

import inspect
import json
import logging
import re
from collections.abc import Awaitable, Callable, Mapping
from functools import wraps
from hashlib import sha256
from time import perf_counter
from typing import Any, TypeVar

from opentelemetry import trace
from opentelemetry.trace import Span, SpanKind, Status, StatusCode

from app.core.config import Settings, get_settings


logger = logging.getLogger(__name__)
T = TypeVar("T")

_configured = False
_provider = None
_SENSITIVE_KEYS = {
    "api_key",
    "approval_token",
    "authorization",
    "content",
    "customer_name",
    "message",
    "password",
    "prompt",
    "secret",
    "token",
}


def configure_tracing(settings: Settings | None = None) -> bool:
    """Configure one OpenTelemetry provider without making tracing a startup dependency."""
    global _configured, _provider
    settings = settings or get_settings()
    if _configured:
        return True
    if not settings.otel_enabled:
        return False

    try:
        from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
        from opentelemetry.sdk.resources import SERVICE_NAME, SERVICE_VERSION, Resource
        from opentelemetry.sdk.trace import TracerProvider
        from opentelemetry.sdk.trace.export import (
            BatchSpanProcessor,
            ConsoleSpanExporter,
            SimpleSpanProcessor,
        )
        from opentelemetry.sdk.trace.sampling import ParentBased, TraceIdRatioBased

        provider = TracerProvider(
            resource=Resource.create(
                {
                    SERVICE_NAME: settings.otel_service_name,
                    SERVICE_VERSION: "0.1.0",
                    "deployment.environment.name": settings.app_env,
                    "resolveai.prompt.version": settings.prompt_version,
                }
            ),
            sampler=ParentBased(TraceIdRatioBased(settings.otel_sample_ratio)),
        )
        if settings.otel_exporter_otlp_traces_endpoint:
            provider.add_span_processor(
                BatchSpanProcessor(
                    OTLPSpanExporter(
                        endpoint=settings.otel_exporter_otlp_traces_endpoint,
                        timeout=settings.otel_export_timeout_seconds,
                    )
                )
            )
        if settings.otel_console_exporter:
            provider.add_span_processor(SimpleSpanProcessor(ConsoleSpanExporter()))
        trace.set_tracer_provider(provider)
        _provider = provider
        _configured = True
        logger.info(
            "OpenTelemetry tracing enabled for %s (sample ratio %.2f)",
            settings.otel_service_name,
            settings.otel_sample_ratio,
        )
        return True
    except Exception:
        logger.exception("OpenTelemetry initialization failed; application will continue")
        return False


def flush_tracing() -> bool:
    """Flush before an RQ work-horse exits; telemetry failure must not fail a job."""
    if _provider is None:
        return True
    try:
        return bool(_provider.force_flush(timeout_millis=5000))
    except Exception:
        logger.warning("OpenTelemetry flush failed; application will continue", exc_info=True)
        return False


def shutdown_tracing() -> None:
    provider = _provider
    if provider is None:
        return
    try:
        provider.force_flush(timeout_millis=5000)
        provider.shutdown()
    except Exception:
        logger.exception("OpenTelemetry shutdown failed")


def get_tracer(component: str = "workflow"):
    return trace.get_tracer(f"resolveai.{component}", "0.1.0")


def current_trace_id() -> str | None:
    context = trace.get_current_span().get_span_context()
    if not context.is_valid:
        return None
    return f"{context.trace_id:032x}"


def trace_url(trace_id: str | None, settings: Settings | None = None) -> str | None:
    if not trace_id:
        return None
    settings = settings or get_settings()
    return f"{settings.otel_ui_url.rstrip('/')}/trace/{trace_id}"


def hash_text(value: str) -> str:
    return sha256(value.encode("utf-8")).hexdigest()[:16]


def safe_payload(value: Any, *, max_length: int = 4000) -> str:
    """Serialize structured telemetry while excluding prompts, PII, secrets and tokens."""

    def clean(item: Any, key: str | None = None) -> Any:
        normalized_key = (key or "").lower()
        if any(marker in normalized_key for marker in _SENSITIVE_KEYS):
            return "[REDACTED]"
        if isinstance(item, Mapping):
            return {str(name): clean(child, str(name)) for name, child in item.items()}
        if isinstance(item, (list, tuple, set)):
            return [clean(child) for child in item]
        if isinstance(item, (str, int, float, bool)) or item is None:
            return item
        return str(item)

    serialized = json.dumps(clean(value), ensure_ascii=False, default=str, sort_keys=True)
    return serialized[:max_length]


def safe_error_message(error: BaseException, *, max_length: int = 300) -> str:
    message = str(error).strip()
    message = re.sub(r"\bsk-[A-Za-z0-9_-]+", "sk-[REDACTED]", message)
    message = re.sub(
        r"(?i)(api[_-]?key|approval[_-]?token|authorization|password|secret|token)"
        r"\s*[:=]\s*[^\s,;]+",
        r"\1=[REDACTED]",
        message,
    )
    return message[:max_length] or type(error).__name__


def set_span_attributes(span: Span, attributes: Mapping[str, Any]) -> None:
    if not span.is_recording():
        return
    for key, value in attributes.items():
        if value is None:
            continue
        if isinstance(value, (str, bool, int, float)):
            span.set_attribute(key, value)
        elif isinstance(value, (list, tuple)) and all(
            isinstance(item, (str, bool, int, float)) for item in value
        ):
            span.set_attribute(key, list(value))
        else:
            span.set_attribute(key, safe_payload(value))


def record_error(span: Span, error: BaseException) -> None:
    if not span.is_recording():
        return
    message = safe_error_message(error)
    span.add_event(
        "exception",
        {"exception.type": type(error).__name__, "exception.message": message},
    )
    span.set_status(Status(StatusCode.ERROR, f"{type(error).__name__}: {message}"))


def traced_workflow(operation: str):
    """Trace a WorkflowService async method and preserve the active context for all graph tasks."""

    def decorator(function: Callable[..., Awaitable[T]]) -> Callable[..., Awaitable[T]]:
        @wraps(function)
        async def wrapper(self, ticket, *args, **kwargs):
            tracer = get_tracer("workflow")
            attributes = {
                "resolveai.workflow.operation": operation,
                "resolveai.ticket.id": ticket.id,
                "resolveai.ticket.no": ticket.ticket_no,
                "resolveai.ticket.status_before": ticket.status,
            }
            with tracer.start_as_current_span(
                f"resolveai.workflow.{operation}",
                kind=SpanKind.INTERNAL,
                attributes=attributes,
                record_exception=False,
                set_status_on_exception=False,
            ) as span:
                trace_id = current_trace_id()
                set_span_attributes(
                    span,
                    {
                        "resolveai.trace.id": trace_id,
                        "resolveai.trace.url": trace_url(trace_id),
                    },
                )
                try:
                    result = await function(self, ticket, *args, **kwargs)
                    set_span_attributes(
                        span,
                        {
                            "resolveai.ticket.status_after": result.status,
                            "resolveai.workflow.requires_approval": result.requires_approval,
                        },
                    )
                    span.set_status(Status(StatusCode.OK))
                    return result
                except BaseException as error:
                    record_error(span, error)
                    raise

        return wrapper

    return decorator


def traced_agent_node(name: str, function: Callable[..., Any]) -> Callable[..., Any]:
    """Wrap sync or async LangGraph nodes with uniform node-level spans."""

    def attributes_from_state(state: Mapping[str, Any]) -> dict[str, Any]:
        ticket = state.get("ticket", {})
        return {
            "resolveai.agent.node": name,
            "resolveai.ticket.id": ticket.get("id"),
            "resolveai.ticket.no": ticket.get("ticket_no"),
            "resolveai.agent.generation_attempt": state.get("generation_attempt", 0),
        }

    def finish(span: Span, result: Mapping[str, Any], started: float) -> None:
        attributes: dict[str, Any] = {
            "resolveai.duration_ms": round((perf_counter() - started) * 1000, 3),
            "resolveai.agent.failure_count": len(result.get("failures", [])),
        }
        if result.get("triage"):
            attributes.update(
                {
                    "resolveai.triage.category": result["triage"].get("category"),
                    "resolveai.triage.priority": result["triage"].get("priority"),
                    "resolveai.triage.confidence": result["triage"].get("confidence"),
                }
            )
        if result.get("citations") is not None:
            attributes["resolveai.rag.document_count"] = len(result.get("citations", []))
        if result.get("customer_context"):
            attributes.update(
                {
                    "resolveai.tool.source": result["customer_context"].get("tool_source"),
                    "resolveai.tool.retry_count": result["customer_context"].get(
                        "tool_retry_count", 0
                    ),
                }
            )
        if result.get("policy"):
            attributes["resolveai.policy.must_review"] = result["policy"].get("must_review")
        if result.get("resolution"):
            attributes.update(
                {
                    "resolveai.resolution.requires_approval": result["resolution"].get(
                        "requires_approval"
                    ),
                    "resolveai.resolution.confidence": result["resolution"].get("confidence"),
                }
            )
        if result.get("quality"):
            attributes.update(
                {
                    "resolveai.quality.passed": result["quality"].get("passed"),
                    "resolveai.quality.regeneration_count": result["quality"].get(
                        "regeneration_count", 0
                    ),
                }
            )
        if result.get("gate"):
            attributes["resolveai.approval.state"] = result["gate"].get("state")
        set_span_attributes(span, attributes)
        span.set_status(Status(StatusCode.OK))

    def interrupted(error: BaseException) -> bool:
        return type(error).__name__ in {"GraphInterrupt", "NodeInterrupt"}

    if inspect.iscoroutinefunction(function):
        @wraps(function)
        async def async_wrapper(state, *args, **kwargs):
            started = perf_counter()
            with get_tracer("agents").start_as_current_span(
                f"resolveai.agent.{name}",
                attributes=attributes_from_state(state),
                record_exception=False,
                set_status_on_exception=False,
            ) as span:
                try:
                    result = await function(state, *args, **kwargs)
                    finish(span, result, started)
                    return result
                except BaseException as error:
                    if interrupted(error):
                        span.add_event("resolveai.workflow.interrupted")
                        set_span_attributes(
                            span,
                            {"resolveai.duration_ms": round((perf_counter() - started) * 1000, 3)},
                        )
                    else:
                        record_error(span, error)
                    raise

        return async_wrapper

    @wraps(function)
    def sync_wrapper(state, *args, **kwargs):
        started = perf_counter()
        with get_tracer("agents").start_as_current_span(
            f"resolveai.agent.{name}",
            attributes=attributes_from_state(state),
            record_exception=False,
            set_status_on_exception=False,
        ) as span:
            try:
                result = function(state, *args, **kwargs)
                finish(span, result, started)
                return result
            except BaseException as error:
                if interrupted(error):
                    span.add_event("resolveai.workflow.interrupted")
                    set_span_attributes(
                        span,
                        {"resolveai.duration_ms": round((perf_counter() - started) * 1000, 3)},
                    )
                else:
                    record_error(span, error)
                raise

    return sync_wrapper
