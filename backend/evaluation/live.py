"""Opt-in live evaluation helpers. Importing this module never calls an API."""

import json
import math
import os
import time
from dataclasses import dataclass, field
from hashlib import sha256
from pathlib import Path
from urllib.parse import urlsplit

from dotenv import dotenv_values

from app.core.config import BACKEND_DIR, Settings
from app.core.observability import safe_error_message
from app.services.llm import LLMService


LIVE_CONFIG_FIELDS = {
    "deepseek_api_key", "deepseek_base_url", "deepseek_model", "deepseek_fast_model",
    "noncritical_model_stages", "prompt_version", "model_timeout_seconds",
    "model_max_retries", "retry_backoff_seconds", "model_primary_max_output_tokens",
    "model_fast_max_output_tokens", "model_max_context_chars",
    "agent_max_conversation_messages", "agent_max_message_chars", "rag_max_documents",
    "rag_snippet_chars", "rag_min_score", "embedding_dimensions",
    "quality_max_regenerations", "failure_escalation_threshold",
    "model_input_cost_per_million_usd", "model_output_cost_per_million_usd",
    "fast_model_input_cost_per_million_usd", "fast_model_output_cost_per_million_usd",
    "embedding_provider", "semantic_embedding_model", "embedding_cache_dir", "semantic_rag_min_score",
}


def evaluation_settings(env_file: Path | None, *, require_key: bool, embedding_provider=None, embedding_cache_dir=None, prompt_version=None) -> Settings:
    """Read only model/RAG options, never inherit a business DB or external services."""
    if env_file is not None and not env_file.is_file():
        raise ValueError("指定的评测配置文件不存在")
    config = dotenv_values(env_file, interpolate=False) if env_file else {}
    values = {name: item.default for name, item in Settings.model_fields.items()}
    values.update(model_max_retries=1)
    for name in LIVE_CONFIG_FIELDS:
        key = name.upper()
        value = os.environ.get(key, config.get(key))
        if value is not None and value != "":
            values[name] = value
    values.update(
        agent_mode="deepseek" if require_key else "demo",
        app_env="test", database_url="sqlite://", mcp_enabled=False,
        async_tasks_enabled=False, retrieval_cache_enabled=False, otel_enabled=False,
        otel_console_exporter=False, openai_api_key=None,
    )
    if embedding_provider is not None:
        values["embedding_provider"] = embedding_provider
    if embedding_cache_dir is not None:
        values["embedding_cache_dir"] = str(embedding_cache_dir.resolve())
    if prompt_version is not None:
        values["prompt_version"] = prompt_version
    if require_key and not values.get("deepseek_api_key"):
        raise ValueError("缺少 DEEPSEEK_API_KEY，请在本地配置文件中设置；不要将 Key 发到聊天中")
    try:
        settings = Settings(_env_file=None, **values)
    except Exception:
        # Pydantic validation errors can include the complete input (including keys).
        raise ValueError("模型或评测参数无效，请检查配置字段类型和范围") from None
    endpoint = urlsplit(settings.deepseek_base_url)
    if (
        endpoint.scheme != "https" or endpoint.hostname != "api.deepseek.com"
        or endpoint.username or endpoint.password or endpoint.query or endpoint.fragment
        or endpoint.port not in (None, 443) or endpoint.path.rstrip("/") not in ("", "/v1")
    ):
        raise ValueError("真实评测只允许 DeepSeek 官方 HTTPS 地址，防止 Key 被发往其他服务")
    return settings


def default_env_file() -> Path | None:
    for candidate in (BACKEND_DIR / ".env.eval", BACKEND_DIR / ".env"):
        if candidate.is_file():
            return candidate
    return None


def public_config(settings: Settings) -> dict:
    return {
        name: getattr(settings, name)
        for name in sorted(LIVE_CONFIG_FIELDS - {"deepseek_api_key"})
    }


class LiveCallError(RuntimeError):
    def __init__(self, message: str, status_code: int | None = None):
        super().__init__(message)
        self.status_code = status_code


class StrictModelFailure(RuntimeError):
    pass


@dataclass
class LiveRun:
    max_api_calls: int
    calls: list[dict] = field(default_factory=list)
    abort_reason: str | None = None

    def claim_call(self) -> None:
        if len(self.calls) >= self.max_api_calls:
            self.abort_reason = "达到 API 调用次数上限，停止本轮评测"
            raise LiveCallError(self.abort_reason)


class EvaluationLLM(LLMService):
    """Use the product's prompts/routing but forbid a successful rules fallback."""

    def __init__(self, settings: Settings, run: LiveRun, case_id: str):
        if settings.agent_mode != "deepseek" or not settings.deepseek_api_key:
            raise ValueError("严格真实评测只接受已配置Key的DeepSeek模式")
        super().__init__(settings)
        self.run = run
        self.case_id = case_id
        self.records: list[dict] = []
        self.triage_category: str | None = None

    def _should_retry(self, error: Exception) -> bool:
        return not self.run.abort_reason

    def _call_with_retry(self, stage, schema, system_prompt, user_prompt):
        result = super()._call_with_retry(stage, schema, system_prompt, user_prompt)
        if result is None:
            raise StrictModelFailure(f"{stage} 真实模型调用失败；严格评测禁止使用规则结果计分")
        return result

    def _parse(self, schema, system_prompt, user_prompt):
        self.run.claim_call()
        started = time.perf_counter()
        record = {
            "case_id": self.case_id, "stage": self._current_stage,
            "attempt": 1 + sum(r["stage"] == self._current_stage for r in self.records),
            "provider": "deepseek", "requested_model": self._current_model,
            "prompt_version": self.settings.prompt_version,
            "system_prompt_sha256": sha256(system_prompt.encode()).hexdigest(),
            "user_prompt_sha256": sha256(user_prompt.encode()).hexdigest(),
            "schema_sha256": sha256(json.dumps(
                schema.model_json_schema(), ensure_ascii=False, sort_keys=True,
            ).encode()).hexdigest(),
            "status": "error", "error": None,
        }
        self.records.append(record)
        self.run.calls.append(record)
        try:
            result = super()._parse(schema, system_prompt, user_prompt)
            if (
                not self._last_response_id or not self._last_response_model
                or not self._last_response_model.lower().startswith("deepseek")
                or not self._last_usage_available or self._last_finish_reason != "stop"
            ):
                raise LiveCallError("响应缺少模型/ID/Token凭据，或输出未正常结束")
            record["status"] = "success"
            if self._current_stage == "triage":
                self.triage_category = result.category
            return result
        except Exception as error:
            status_code = getattr(error, "status_code", None)
            # Authentication, model/parameter configuration and quota errors are fatal.
            if status_code in {400, 401, 402, 403, 404, 422}:
                self.run.abort_reason = f"DeepSeek 配置/认证/额度错误（HTTP {status_code}），停止评测"
            message = safe_error_message(error)
            if self.settings.deepseek_api_key:
                message = message.replace(self.settings.deepseek_api_key, "[REDACTED]")
            record["error"] = f"{type(error).__name__}: {message}"
            record["status_code"] = status_code
            raise LiveCallError(record["error"], status_code) from None
        finally:
            from app.services.costs import estimate_cost
            usage_known = self._last_usage_available
            record.update(
                response_id=self._last_response_id, response_model=self._last_response_model,
                finish_reason=self._last_finish_reason,
                duration_ms=round((time.perf_counter() - started) * 1000, 3),
                usage_available=usage_known, **self._last_usage,
                **estimate_cost(self.settings, self._last_response_model or self._current_model,
                                {**self._last_usage, "stage": self._current_stage}, usage_available=usage_known),
            )

    def valid_live_result(self) -> bool:
        return bool(self.call_metadata) and all(
            item.get("provider") == "deepseek" and item.get("fallback") is False
            and any(r["stage"] == stage and r["status"] == "success" for r in self.records)
            for stage, item in self.call_metadata.items()
        )


def summarize_calls(run: LiveRun) -> dict:
    calls = run.calls
    durations = sorted(call["duration_ms"] for call in calls)

    def percentile(p: float) -> float | None:
        return durations[max(0, math.ceil(len(durations) * p) - 1)] if durations else None

    known_costs = [call["estimated_cost_usd"] for call in calls if call["estimated_cost_usd"] is not None]
    return {
        "api_calls": len(calls), "max_api_calls": run.max_api_calls,
        "successful_calls": sum(call["status"] == "success" for call in calls),
        "failed_calls": sum(call["status"] != "success" for call in calls),
        "api_failure_rate": (
            round(sum(call["status"] != "success" for call in calls) / len(calls), 4)
            if calls else None
        ),
        "usage_unknown_calls": sum(not call["usage_available"] for call in calls),
        "input_tokens": sum(call.get("input_tokens", 0) for call in calls),
        "output_tokens": sum(call.get("output_tokens", 0) for call in calls),
        "total_tokens": sum(call.get("total_tokens", 0) for call in calls),
        "p50_ms": percentile(0.5), "p95_ms": percentile(0.95),
        "known_estimated_cost_usd": round(sum(known_costs), 8) if known_costs else None,
        "cost_complete": bool(calls) and len(known_costs) == len(calls),
        "cost_lower_usd": round(sum(call.get("cost_lower_usd") or 0 for call in calls), 8) if calls and len(known_costs) == len(calls) else None,
        "cost_upper_usd": round(sum(call.get("cost_upper_usd") or 0 for call in calls), 8) if calls and len(known_costs) == len(calls) else None,
        "cost_note": "按版本化官方费率估算峰谷区间；所有重试计入，未知不按0处理；不是平台账单",
        "abort_reason": run.abort_reason,
    }
