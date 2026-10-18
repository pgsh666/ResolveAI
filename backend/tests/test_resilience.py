import asyncio

from fastapi.testclient import TestClient

from app.core.config import Settings
from app.main import app
from app.schemas import ResolutionResult
from app.services.commerce import CommerceGateway
from app.services.llm import LLMService
from app.services.retrieval import RetrievalService


def test_model_timeout_retries_then_uses_deterministic_fallback(monkeypatch):
    settings = Settings(
        _env_file=None,
        app_env="test",
        agent_mode="deepseek",
        deepseek_api_key="test-key",
        model_max_retries=2,
        retry_backoff_seconds=0,
    )
    service = LLMService(settings)
    calls = 0

    def always_timeout(*args, **kwargs):
        nonlocal calls
        calls += 1
        raise TimeoutError("simulated model timeout")

    monkeypatch.setattr(service, "_parse", always_timeout)
    result = service.triage("物流异常", "物流三天没有更新")

    assert result.category == "logistics"
    assert calls == 3
    assert service.call_metadata["triage"]["fallback"] is True
    assert service.call_metadata["triage"]["attempts"] == 3
    assert service.call_metadata["triage"]["retry_count"] == 2
    assert len(service.call_metadata["triage"]["errors"]) == 3
    assert "TimeoutError" in service.call_metadata["triage"]["failure_reason"]


def test_mcp_failure_retries_then_returns_auditable_local_fallback(monkeypatch):
    settings = Settings(
        _env_file=None,
        app_env="test",
        agent_mode="demo",
        mcp_enabled=True,
        tool_max_retries=2,
        retry_backoff_seconds=0,
    )
    gateway = CommerceGateway(settings)
    calls = 0

    async def always_timeout(tool_name, arguments):
        nonlocal calls
        calls += 1
        raise TimeoutError(f"{tool_name} simulated timeout")

    monkeypatch.setattr(gateway, "_call_mcp", always_timeout)
    context = asyncio.run(gateway.aget_customer_context("ORD-RETRY-01", "重试测试用户"))

    assert calls == 3
    assert context["tool_source"] == "mcp-local-adapter"
    assert context["tool_attempts"] == 3
    assert context["tool_retry_count"] == 2
    assert len(context["tool_errors"]) == 3
    assert "MCP unavailable" in context["tool_fallback"]


def test_rag_without_reliable_evidence_refuses_to_invent_and_escalates(monkeypatch):
    monkeypatch.setattr(
        RetrievalService,
        "search",
        lambda self, query, limit=3: [],
    )

    with TestClient(app) as client:
        created = client.post(
            "/api/v1/tickets",
            json={
                "customer_name": "无答案测试用户",
                "subject": "未知设备咨询",
                "message": "请解释火星量子传送器的退款规则。",
                "order_id": "ORD-NO-RAG-01",
                "amount": 20,
            },
        ).json()
        analyzed = client.post(f"/api/v1/tickets/{created['id']}/analyze")

        assert analyzed.status_code == 200, analyzed.text
        detail = analyzed.json()
        assert detail["status"] == "pending_approval"
        assert "还需要进一步核实" in detail["draft_response"]
        assert "人工客服" in detail["draft_response"]
        assert "知识库" not in detail["draft_response"]
        assert detail["assessment"]["resolution"]["safe_refusal"] is True
        assert detail["assessment"]["resilience"]["rag"]["reliable"] is False
        assert detail["assessment"]["resilience"]["failure_count"] >= 1
        assert any(
            item["code"] == "RAG_NO_RELIABLE_RESULT"
            for item in detail["assessment"]["resilience"]["failures"]
        )
        assert "RAG_NO_RELIABLE_RESULT" in detail["approval"]["risk_reason"]


def test_quality_failure_regenerates_once_then_passes(monkeypatch):
    calls = 0

    def flaky_resolution(self, ticket, customer_context, citations, policy):
        nonlocal calls
        calls += 1
        self.call_metadata["resolution"] = {
            "provider": "test",
            "model": "quality-test",
            "fallback": False,
            "attempts": 1,
            "retry_count": 0,
            "timeout_seconds": 1,
            "errors": [],
        }
        return ResolutionResult(
            recommended_action="提供退款进度说明",
            rationale="依据退款到账政策处理",
            draft_response=(
                "太短"
                if calls == 1
                else "您好，我们已经根据退款到账政策核查您的订单，并会继续同步处理进度。"
            ),
            refund_amount=None,
            requires_approval=False,
            confidence=0.8,
        )

    monkeypatch.setattr(LLMService, "resolve", flaky_resolution)
    with TestClient(app) as client:
        created = client.post(
            "/api/v1/tickets",
            json={
                "customer_name": "质检重生成用户",
                "subject": "商品使用说明",
                "message": "请根据商品说明提供使用建议。",
                "order_id": "ORD-QUALITY-01",
                "amount": 20,
            },
        ).json()
        analyzed = client.post(f"/api/v1/tickets/{created['id']}/analyze")

        assert analyzed.status_code == 200, analyzed.text
        detail = analyzed.json()
        assert calls == 2
        assert detail["status"] == "in_progress"
        assert detail["assessment"]["quality"]["passed"] is True
        assert detail["assessment"]["resilience"]["quality_regeneration_count"] == 1
        assert any(
            item["code"] == "QUALITY_CHECK_FAILED"
            for item in detail["assessment"]["resilience"]["failures"]
        )


def test_repeated_quality_failure_escalates_to_human(monkeypatch):
    calls = 0

    def always_invalid_resolution(self, ticket, customer_context, citations, policy):
        nonlocal calls
        calls += 1
        self.call_metadata["resolution"] = {
            "provider": "test",
            "model": "quality-test",
            "fallback": False,
            "attempts": 1,
            "retry_count": 0,
            "timeout_seconds": 1,
            "errors": [],
        }
        return ResolutionResult(
            recommended_action="处理咨询",
            rationale="测试连续质量失败",
            draft_response="太短",
            requires_approval=False,
            confidence=0.3,
        )

    monkeypatch.setattr(LLMService, "resolve", always_invalid_resolution)
    with TestClient(app) as client:
        created = client.post(
            "/api/v1/tickets",
            json={
                "customer_name": "连续失败测试用户",
                "subject": "物流延误",
                "message": "快递已经三天没有更新，请帮我处理。",
                "order_id": "ORD-QUALITY-FAIL-01",
            },
        ).json()
        analyzed = client.post(f"/api/v1/tickets/{created['id']}/analyze")

        assert analyzed.status_code == 200, analyzed.text
        detail = analyzed.json()
        assert calls == 2
        assert detail["status"] == "pending_approval"
        assert detail["assessment"]["quality"]["escalated"] is True
        assert detail["assessment"]["resilience"]["failure_count"] >= 2
        assert detail["assessment"]["resilience"]["escalated_to_human"] is True
        assert detail["recommended_action"].startswith("转人工复核")
