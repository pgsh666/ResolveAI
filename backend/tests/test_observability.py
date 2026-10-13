from opentelemetry import trace
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter
from fastapi.testclient import TestClient

from app.core.config import Settings
from app.core.observability import get_tracer, safe_payload, traced_agent_node
from app.services.llm import LLMService
from app.main import app


SPAN_EXPORTER = InMemorySpanExporter()
TRACE_PROVIDER = TracerProvider()
TRACE_PROVIDER.add_span_processor(SimpleSpanProcessor(SPAN_EXPORTER))
trace.set_tracer_provider(TRACE_PROVIDER)


def test_telemetry_payload_redacts_prompts_pii_and_credentials():
    payload = safe_payload(
        {
            "order_id": "ORD-1001",
            "amount": 88,
            "message": "我的手机号是 13800138000",
            "approval_token": "signed-secret",
            "nested": {"api_key": "sk-secret", "currency": "CNY"},
        }
    )

    assert "ORD-1001" in payload
    assert '"amount": 88' in payload
    assert "13800138000" not in payload
    assert "signed-secret" not in payload
    assert "sk-secret" not in payload
    assert payload.count("[REDACTED]") == 3


def test_model_metadata_calculates_tokens_retries_and_cost():
    settings = Settings(
        app_env="test",
        agent_mode="openai",
        openai_api_key="unit-test-no-network",
        database_url="sqlite://",
        model_input_cost_per_million_usd=2,
        model_output_cost_per_million_usd=8,
    )
    service = LLMService(settings)

    metadata = service._metadata(
        fallback=False,
        attempts=2,
        usage={"input_tokens": 1000, "output_tokens": 500, "total_tokens": 1500},
        duration_ms=12.5,
    )

    assert metadata["prompt_version"] == settings.prompt_version
    assert metadata["retry_count"] == 1
    assert metadata["total_tokens"] == 1500
    assert metadata["estimated_cost_usd"] == 0.006
    assert metadata["duration_ms"] == 12.5


def test_observability_endpoint_discloses_config_but_not_secrets():
    with TestClient(app) as client:
        response = client.get("/api/v1/observability")

    assert response.status_code == 200
    payload = response.json()
    assert payload["provider"] == "opentelemetry"
    assert payload["service_name"] == "resolveai-backend"
    assert payload["privacy"] == {
        "raw_prompts_exported": False,
        "raw_customer_messages_exported": False,
        "secrets_exported": False,
    }
    assert "endpoint" not in payload


def test_agent_node_span_is_child_of_workflow_span():
    SPAN_EXPORTER.clear()

    node = traced_agent_node(
        "triage_agent",
        lambda state: {
            "triage": {
                "category": "refund",
                "priority": "high",
                "confidence": 0.91,
            },
            "failures": [],
        },
    )
    with get_tracer("test").start_as_current_span("resolveai.workflow.test"):
        result = node(
            {
                "ticket": {"id": "ticket-1", "ticket_no": "TKT-1"},
                "generation_attempt": 0,
            }
        )

    spans = SPAN_EXPORTER.get_finished_spans()
    root = next(span for span in spans if span.name == "resolveai.workflow.test")
    child = next(span for span in spans if span.name == "resolveai.agent.triage_agent")
    assert result["triage"]["category"] == "refund"
    assert child.parent is not None
    assert child.parent.span_id == root.context.span_id
    assert child.attributes["resolveai.triage.category"] == "refund"
    assert child.attributes["resolveai.duration_ms"] >= 0


def test_model_retry_errors_are_visible_in_span(monkeypatch):
    SPAN_EXPORTER.clear()
    settings = Settings(
        app_env="test",
        agent_mode="deepseek",
        deepseek_api_key="test-only-key",
        database_url="sqlite://",
        model_max_retries=2,
        retry_backoff_seconds=0,
    )
    service = LLMService(settings)

    def always_timeout(*_args, **_kwargs):
        raise TimeoutError("simulated timeout")

    monkeypatch.setattr(service, "_parse", always_timeout)
    result = service.triage("退款", "申请退款")

    model_span = next(
        span
        for span in SPAN_EXPORTER.get_finished_spans()
        if span.name == "resolveai.llm.triage"
    )
    assert result.category == "refund"
    assert service.call_metadata["triage"]["fallback"] is True
    assert model_span.attributes["resolveai.model.attempts"] == 3
    assert model_span.attributes["resolveai.model.retry_count"] == 2
    assert model_span.attributes["resolveai.model.fallback"] is True
    assert len(model_span.events) == 3
    assert all(event.name == "resolveai.model.attempt_failed" for event in model_span.events)
