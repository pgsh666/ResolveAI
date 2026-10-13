from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select

from app.core.database import SessionLocal
from app.main import app
from app.models import ToolExecution


pytestmark = pytest.mark.e2e


def test_customer_refund_approval_tool_and_confirmation_flow():
    idempotency_key = f"e2e-refund:{uuid4().hex}"
    with TestClient(app) as client:
        created = client.post(
            "/api/v1/customer/tickets",
            json={
                "customer_name": "端到端测试用户",
                "subject": "质量问题退款",
                "message": "商品存在质量问题，希望退款180元。",
                "order_id": "ORD-E2E-221",
                "amount": 180,
            },
        )
        assert created.status_code == 201, created.text
        ticket_id = created.json()["id"]

        analyzed = client.post(f"/api/v1/tickets/{ticket_id}/analyze")
        assert analyzed.status_code == 200, analyzed.text
        assert analyzed.json()["status"] == "pending_approval"
        assert analyzed.json()["approval"]["state"] == "pending"

        approved = client.post(
            f"/api/v1/tickets/{ticket_id}/decision",
            json={"decision": "approve", "reviewer": "E2E审核员", "note": "同意退款"},
        )
        assert approved.status_code == 200, approved.text
        approved_detail = approved.json()
        assert approved_detail["approval"]["state"] == "approved"
        assert approved_detail["assessment"]["gate"]["state"] == "approved"

        executed = client.post(
            f"/api/v1/tickets/{ticket_id}/tools/execute_refund/execute",
            json={
                "idempotency_key": idempotency_key,
                "arguments": {
                    "order_id": "ORD-E2E-221",
                    "amount": 180,
                    "currency": "CNY",
                    "reason": "商品质量问题退款",
                },
            },
        )
        assert executed.status_code == 200, executed.text
        assert executed.json()["status"] == "completed"
        assert executed.json()["result"]["success"] is True

        duplicate = client.post(
            f"/api/v1/tickets/{ticket_id}/tools/execute_refund/execute",
            json={
                "idempotency_key": idempotency_key,
                "arguments": {
                    "order_id": "ORD-E2E-221",
                    "amount": 180,
                    "currency": "CNY",
                    "reason": "商品质量问题退款",
                },
            },
        )
        assert duplicate.status_code == 200
        assert duplicate.json()["replayed"] is True
        assert duplicate.json()["id"] == executed.json()["id"]

        sent = client.post(
            f"/api/v1/tickets/{ticket_id}/draft/send",
            json={"response": "您好，人工审批已通过，本次项目模拟退款180元已执行，不代表真实资金到账。"},
        )
        assert sent.status_code == 200, sent.text
        assert sent.json()["status"] == "awaiting_customer"

        confirmed = client.post(
            f"/api/v1/customer/tickets/{ticket_id}/confirmation",
            json={"resolved": True, "rating": 5, "feedback": "退款方案已确认。"},
        )
        assert confirmed.status_code == 200, confirmed.text
        assert confirmed.json()["stage"] == "resolved"

    with SessionLocal() as session:
        execution_count = session.scalar(
            select(func.count(ToolExecution.id)).where(
                ToolExecution.idempotency_key == idempotency_key
            )
        )
    assert execution_count == 1
