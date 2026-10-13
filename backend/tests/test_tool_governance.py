import asyncio
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError
from sqlalchemy import func, select

from app.core.database import SessionLocal
from app.main import app
from app.models import Approval, Ticket, ToolExecution, utc_now
from app.services.commerce import CommerceGateway
from app.tools.authorization import ApprovalTokenError
from app.tools.contracts import ToolPermission, ToolPermissionError, validate_tool_call


def test_tool_contracts_reject_invalid_and_unauthorized_calls():
    with pytest.raises(ValidationError):
        validate_tool_call(
            "calculate_refund_quote",
            {"order_id": "", "requested_amount": -1, "unexpected": True},
            {ToolPermission.READ},
        )

    with pytest.raises(ToolPermissionError):
        validate_tool_call(
            "get_order_context",
            {"order_id": "ORD-8821"},
            set(),
        )


def test_write_gateway_requires_signed_approval_credential():
    with pytest.raises(ApprovalTokenError):
        asyncio.run(
            CommerceGateway().acall_tool(
                "execute_refund",
                {
                    "order_id": "ORD-SECURE-01",
                    "amount": 20,
                    "currency": "CNY",
                    "reason": "质量问题",
                },
                granted_permissions={ToolPermission.WRITE},
            )
        )


def test_tool_catalog_and_idempotent_approved_write():
    with TestClient(app) as client:
        catalog_response = client.get("/api/v1/tools")
        assert catalog_response.status_code == 200
        catalog = {item["name"]: item for item in catalog_response.json()["tools"]}
        assert catalog["get_order_context"]["kind"] == "query"
        assert catalog["execute_refund"]["kind"] == "write"
        assert catalog["execute_refund"]["permission"] == "tools:write"
        assert catalog["execute_refund"]["input_schema"]["additionalProperties"] is False
        assert set(catalog["execute_refund"]["input_schema"]["required"]) >= {
            "order_id",
            "amount",
            "reason",
        }

        suffix = uuid4().hex[:8]
        ticket = Ticket(
            ticket_no=f"TOOL-{suffix}",
            customer_name="工具测试用户",
            subject="测试审批退款",
            message="需要执行一笔审批后的退款",
            order_id=f"ORD-{suffix}",
            amount=120,
        )
        with SessionLocal() as session:
            session.add(ticket)
            session.commit()
            session.refresh(ticket)
            ticket_id = ticket.id
            order_id = ticket.order_id

        endpoint = f"/api/v1/tickets/{ticket_id}/tools/execute_refund/execute"
        valid_payload = {
            "idempotency_key": f"refund:{ticket_id}",
            "arguments": {
                "order_id": order_id,
                "amount": 100,
                "currency": "CNY",
                "reason": "质量问题退款",
            },
        }

        unauthorized = client.post(endpoint, json=valid_payload)
        assert unauthorized.status_code == 403
        assert "人工审批" in unauthorized.text

        invalid = client.post(
            endpoint,
            json={
                "idempotency_key": f"invalid:{ticket_id}",
                "arguments": {
                    "order_id": order_id,
                    "amount": -1,
                    "reason": "x",
                    "unexpected": True,
                },
            },
        )
        assert invalid.status_code == 422
        assert invalid.json()["detail"]["message"] == "工具参数校验失败"

        with SessionLocal() as session:
            session.add(
                Approval(
                    ticket_id=ticket_id,
                    state="approved",
                    action="原路退款",
                    amount=100,
                    reviewed_by="测试审批人",
                    reviewed_at=utc_now(),
                )
            )
            session.commit()

        first = client.post(endpoint, json=valid_payload)
        assert first.status_code == 200, first.text
        first_result = first.json()
        assert first_result["status"] == "completed"
        assert first_result["replayed"] is False
        assert first_result["result"]["success"] is True

        duplicate = client.post(endpoint, json=valid_payload)
        assert duplicate.status_code == 200, duplicate.text
        duplicate_result = duplicate.json()
        assert duplicate_result["id"] == first_result["id"]
        assert duplicate_result["result"] == first_result["result"]
        assert duplicate_result["replayed"] is True

        different_key_same_action = client.post(
            endpoint,
            json={**valid_payload, "idempotency_key": f"refund-retry:{ticket_id}"},
        )
        assert different_key_same_action.status_code == 200
        assert different_key_same_action.json()["id"] == first_result["id"]
        assert different_key_same_action.json()["replayed"] is True

        conflicting = client.post(
            endpoint,
            json={
                **valid_payload,
                "arguments": {**valid_payload["arguments"], "amount": 99},
            },
        )
        assert conflicting.status_code == 409

        with SessionLocal() as session:
            execution_count = session.scalar(
                select(func.count(ToolExecution.id)).where(
                    ToolExecution.idempotency_key == valid_payload["idempotency_key"]
                )
            )
        assert execution_count == 1
