import asyncio

import pytest
from fastapi.testclient import TestClient

from app.core.config import Settings
from app.core.database import SessionLocal, init_db
from app.main import app
from app.services.commerce import CommerceGateway
from app.services.retrieval import RetrievalService
from app.services.seed import seed_database
from mcp_server.server import get_order_context


pytestmark = pytest.mark.integration


def test_database_seed_and_rag_retrieval_are_connected():
    init_db()
    with SessionLocal() as session:
        seed_database(session)
        citations, status = RetrievalService(session).search_reliable(
            "退款审批与风控规范 单笔退款金额超过100元必须人工审批",
            limit=3,
        )

    assert status["reliable"] is True
    assert citations
    assert any(item.source == "policy://risk/refund-approval" for item in citations)
    assert all(item.id and item.source and item.snippet for item in citations)


def test_gateway_and_real_mcp_tool_contract_integrate(monkeypatch):
    settings = Settings(
        _env_file=None,
        app_env="test",
        agent_mode="demo",
        mcp_enabled=True,
        tool_max_retries=0,
        retry_backoff_seconds=0,
    )
    gateway = CommerceGateway(settings)

    async def in_process_mcp_call(tool_name, arguments):
        assert tool_name == "get_order_context"
        return get_order_context(**arguments)

    monkeypatch.setattr(gateway, "_call_mcp", in_process_mcp_call)
    context = asyncio.run(gateway.aget_customer_context("ORD-INTEGRATION-221", "集成测试用户"))

    assert context["order_id"] == "ORD-INTEGRATION-221"
    assert context["tool_source"] == "mcp-server"
    assert context["tool_attempts"] == 1
    assert context["tool_retry_count"] == 0


def test_all_seven_agent_nodes_persist_audit_events():
    expected_actions = {
        "意图识别与优先级判断",
        "RAG 向量检索",
        "调用 MCP 业务工具",
        "合规与风险校验",
        "生成处置方案与回复",
        "事实性与合规检查",
        "高风险动作门控",
    }
    with TestClient(app) as client:
        created = client.post(
            "/api/v1/tickets",
            json={
                "customer_name": "Agent集成测试用户",
                "subject": "物流延误需要催单",
                "message": "快递三天没有更新，请查询物流并帮我催促。",
                "order_id": "ORD-INTEGRATION-222",
            },
        )
        assert created.status_code == 201, created.text
        analyzed = client.post(f"/api/v1/tickets/{created.json()['id']}/analyze")

    assert analyzed.status_code == 200, analyzed.text
    detail = analyzed.json()
    actions = {event["action"] for event in detail["audit_events"]}
    assert expected_actions.issubset(actions)
    assert detail["assessment"]["citations"]
    assert detail["assessment"]["customer_context"]["order_id"] == "ORD-INTEGRATION-222"
    assert detail["assessment"]["quality"]["passed"] is True
