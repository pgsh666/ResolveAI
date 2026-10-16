from fastapi.testclient import TestClient
from sqlalchemy import func, select

from app.core.database import SessionLocal, engine
from app.main import app
from app.models import (
    AuditEvent,
    LangGraphCheckpoint,
    LangGraphCheckpointWrite,
    WorkflowCheckpoint,
)


def test_checkpoint_survives_restart_and_resumes_after_human_approval():
    with TestClient(app) as client:
        created = client.post(
            "/api/v1/tickets",
            json={
                "customer_name": "Checkpoint 测试用户",
                "subject": "质量问题退款",
                "message": "商品存在质量问题，希望退款。",
                "order_id": "ORD-CHECKPOINT-01",
                "amount": 180,
            },
        )
        assert created.status_code == 201
        ticket_id = created.json()["id"]

        analyzed = client.post(f"/api/v1/tickets/{ticket_id}/analyze")
        assert analyzed.status_code == 200, analyzed.text
        assert analyzed.json()["status"] == "pending_approval"

        checkpoint_response = client.get(f"/api/v1/tickets/{ticket_id}/checkpoint")
        assert checkpoint_response.status_code == 200
        pending_checkpoint = checkpoint_response.json()
        thread_id = pending_checkpoint["thread_id"]
        assert pending_checkpoint["status"] == "waiting_approval"
        assert pending_checkpoint["current_node"] == "approval_gate"
        assert pending_checkpoint["next_node"] == "approval_gate"
        assert pending_checkpoint["checkpoint_id"]
        assert pending_checkpoint["tool_results"][0]["tool_name"] == "get_order_context"
        assert pending_checkpoint["conversation"][0]["role"] == "customer"

    # Dispose every pooled connection and create a fresh API client. The next
    # request must reconstruct the graph from persisted database checkpoints.
    engine.dispose()

    with TestClient(app) as restarted_client:
        decision = restarted_client.post(
            f"/api/v1/tickets/{ticket_id}/decision",
            json={"decision": "approve", "reviewer": "重启恢复测试", "note": "同意"},
        )
        assert decision.status_code == 200, decision.text
        assert decision.json()["approval"]["state"] == "approved"
        assert decision.json()["assessment"]["gate"]["state"] == "approved"

        resumed_response = restarted_client.get(
            f"/api/v1/tickets/{ticket_id}/checkpoint"
        )
        resumed_checkpoint = resumed_response.json()
        assert resumed_checkpoint["thread_id"] == thread_id
        assert resumed_checkpoint["status"] == "completed"
        assert resumed_checkpoint["next_node"] is None
        assert resumed_checkpoint["resumed_at"] is not None
        assert resumed_checkpoint["completed_at"] is not None

    with SessionLocal() as session:
        native_checkpoint_count = session.scalar(
            select(func.count(LangGraphCheckpoint.id)).where(
                LangGraphCheckpoint.thread_id == thread_id
            )
        )
        native_write_count = session.scalar(
            select(func.count(LangGraphCheckpointWrite.id)).where(
                LangGraphCheckpointWrite.thread_id == thread_id
            )
        )
        triage_event_count = session.scalar(
            select(func.count(AuditEvent.id)).where(
                AuditEvent.ticket_id == ticket_id,
                AuditEvent.action == "意图识别与优先级判断",
            )
        )
        gate_event_count = session.scalar(
            select(func.count(AuditEvent.id)).where(
                AuditEvent.ticket_id == ticket_id,
                AuditEvent.action == "高风险动作门控",
            )
        )
    assert native_checkpoint_count and native_checkpoint_count > 1
    assert native_write_count and native_write_count > 0
    assert triage_event_count == 1
    assert gate_event_count == 1


def test_current_conversation_is_kept_as_short_term_memory():
    with TestClient(app) as client:
        created = client.post(
            "/api/v1/customer/tickets",
            json={
                "customer_name": "会话记忆测试用户",
                "subject": "物流查询",
                "message": "请帮我查询现在的物流进度。",
                "order_id": "ORD-MEMORY-01",
            },
        )
        ticket_id = created.json()["id"]
        first_analysis = client.post(f"/api/v1/tickets/{ticket_id}/analyze")
        assert first_analysis.status_code == 200, first_analysis.text

        first_checkpoint = client.get(
            f"/api/v1/tickets/{ticket_id}/checkpoint"
        ).json()
        first_thread_id = first_checkpoint["thread_id"]
        assert first_checkpoint["status"] == "completed"

        sent = client.post(
            f"/api/v1/tickets/{ticket_id}/draft/send",
            json={"response": "我们正在核查物流信息。"},
        )
        assert sent.status_code == 200
        follow_up = client.post(
            f"/api/v1/customer/tickets/{ticket_id}/messages",
            json={"message": "上一轮说在核查，请问现在有结果了吗？"},
        )
        assert follow_up.status_code == 200

        superseded = client.get(f"/api/v1/tickets/{ticket_id}/checkpoint").json()
        assert superseded["status"] == "completed"
        assert superseded["thread_id"] != first_thread_id

        second_analysis = client.post(f"/api/v1/tickets/{ticket_id}/analyze")
        assert second_analysis.status_code == 200, second_analysis.text
        second_checkpoint = client.get(
            f"/api/v1/tickets/{ticket_id}/checkpoint"
        ).json()
        assert second_checkpoint["thread_id"] != first_thread_id
        assert [item["role"] for item in second_checkpoint["conversation"]] == [
            "customer",
            "support",
            "support",
            "customer",
            "support",
        ]
        assert second_checkpoint["state"]["conversation"][-2]["content"] == (
            "上一轮说在核查，请问现在有结果了吗？"
        )

    with SessionLocal() as session:
        workflow = session.scalar(
            select(WorkflowCheckpoint).where(WorkflowCheckpoint.ticket_id == ticket_id)
        )
        assert workflow is not None
        assert len(workflow.conversation) == 5
