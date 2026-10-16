from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from app.core.config import get_settings
from app.core.database import SessionLocal
from app.main import app
from app.models import Account, Ticket
from app.services.auth import PASSWORDS
from app.services.workflow import ticket_customer_feedback


@pytest.mark.parametrize("resolved", [True, False])
@pytest.mark.parametrize("rating", [None, 4])
def test_merchant_detail_exposes_customer_feedback_without_turning_it_into_a_chat_message(resolved, rating):
    with TestClient(app) as client:
        chat = client.post("/api/v1/customer/tickets", json={"message": "您好"}).json()
        feedback = "回复内容清楚。\n还想了解其他商品。"
        confirmation = client.post(f"/api/v1/customer/tickets/{chat['id']}/confirmation",
                                   json={"resolved": resolved, "rating": rating, "feedback": feedback})
        assert confirmation.status_code == 200, confirmation.text
        detail = client.get(f"/api/v1/tickets/{chat['id']}").json()
        assert detail["status"] == ("resolved" if resolved else "needs_review")
        assert len(detail["customer_feedbacks"]) == 1
        row = detail["customer_feedbacks"][0]
        assert row["resolved"] is resolved and row["rating"] == rating
        assert row["feedback"] == feedback and row["created_at"]
        assert any(e["id"] == row["id"] and e["action"] == "确认处理结果" for e in detail["audit_events"])
        assert detail["conversation"] == chat["messages"]


def test_feedback_history_survives_followup_agent_rerun_and_further_manual_replies():
    with TestClient(app) as client:
        chat = client.post("/api/v1/customer/tickets", json={"message": "您好"}).json()
        path = f"/api/v1/tickets/{chat['id']}"
        confirm = f"/api/v1/customer/tickets/{chat['id']}/confirmation"
        assert client.post(confirm, json={"resolved": False, "rating": 2, "feedback": "还没有回答我的选购问题"}).status_code == 200
        initial = client.get(path).json()["customer_feedbacks"]
        follow = client.post(f"/api/v1/customer/tickets/{chat['id']}/messages", json={"message": "商城有哪些商品？"})
        assert follow.status_code == 200, follow.text
        assert follow.json()["customer_confirmed"] is None
        assert client.get(path).json()["customer_feedbacks"] == initial
        analyzed = client.post(path + "/analyze")
        assert analyzed.status_code == 200, analyzed.text
        assert analyzed.json()["customer_feedbacks"] == initial
        sent = client.post(path + "/draft/send", json={"response": "您可以告诉我预算和想选购的商品类型。"})
        assert sent.status_code == 200, sent.text
        assert sent.json()["customer_feedbacks"] == initial
        assert client.post(confirm, json={"resolved": True, "rating": 5, "feedback": "这次说明清楚了，谢谢"}).status_code == 200
        rows = client.get(path).json()["customer_feedbacks"]
        assert len(rows) == 2 and rows[0] == initial[0]
        assert rows[1]["resolved"] and rows[1]["rating"] == 5
        assert rows[1]["feedback"] == "这次说明清楚了，谢谢"


def test_feedback_redacts_sensitive_content_and_does_not_invent_empty_comments():
    with TestClient(app) as client:
        chat = client.post("/api/v1/customer/tickets", json={"message": "您好"}).json()
        confirm = f"/api/v1/customer/tickets/{chat['id']}/confirmation"
        client.post(confirm, json={"resolved": False, "feedback": "联系手机号13812345678，密码：Abcd1234"})
        row = client.get(f"/api/v1/tickets/{chat['id']}").json()["customer_feedbacks"][0]
        assert "13812345678" not in row["feedback"] and "Abcd1234" not in row["feedback"]
        assert "****" in row["feedback"] and "已脱敏" in row["feedback"]
        assert row["rating"] is None
        client.post(confirm, json={"resolved": True, "feedback": ""})
        row = client.get(f"/api/v1/tickets/{chat['id']}").json()["customer_feedbacks"][-1]
        assert row["feedback"] == "" and row["rating"] is None


def test_legacy_feedback_snapshots_are_read_only_deduplicated_and_keep_known_rating():
    timestamp = "2026-10-15T12:00:00+00:00"
    assessment = {"customer_feedback_history": [{"feedback": "需要帮助", "created_at": timestamp}],
                  "customer_confirmation": {"resolved": False, "rating": 3, "feedback": "需要帮助", "confirmed_at": timestamp}}
    ticket = Ticket(id="legacy-feedback", assessment=assessment)
    rows = ticket_customer_feedback(ticket, [])
    assert len(rows) == 1 and rows[0].rating == 3
    assert rows[0].resolved is False and rows[0].feedback == "需要帮助"
    assert ticket.assessment == assessment


def test_legacy_feedback_missing_or_invalid_metadata_stays_unknown():
    ticket = Ticket(id="legacy-unknown", assessment={"customer_confirmation": {
        "feedback": "旧留言", "rating": 0, "confirmed_at": "not-a-date"}})
    row = ticket_customer_feedback(ticket, [])[0]
    assert row.feedback == "旧留言" and row.rating is None
    assert row.created_at is None and row.resolved is None
    assert ticket_customer_feedback(Ticket(id="empty", assessment={}), []) == []


def test_feedback_access_retains_customer_and_workspace_isolation():
    settings = get_settings()
    original = settings.auth_enabled
    settings.auth_enabled = True
    try:
        with TestClient(app, client=("feedback-" + uuid4().hex, 50000)) as client:
            def signup():
                result = client.post("/api/v1/auth/register", json={"username": "feedback" + uuid4().hex[:10],
                    "password": "Fixture-Pass-2026!", "display_name": "反馈客户"})
                assert result.status_code == 201, result.text
                client.headers["X-CSRF-Token"] = result.json()["csrf_token"]
            signup()
            chat = client.post("/api/v1/customer/tickets", json={"message": "您好"}).json()
            client.post(f"/api/v1/customer/tickets/{chat['id']}/confirmation",
                        json={"resolved": False, "rating": 1, "feedback": "本客户私有反馈"})
            assert client.get(f"/api/v1/tickets/{chat['id']}").status_code == 403
            client.post("/api/v1/auth/logout")
            signup()
            assert client.get(f"/api/v1/customer/tickets/{chat['id']}").status_code == 404
            assert client.get(f"/api/v1/tickets/{chat['id']}").status_code == 403
            client.post("/api/v1/auth/logout")
            username = "foreign-feedback" + uuid4().hex[:8]
            with SessionLocal() as session:
                session.add(Account(username=username, display_name="另一工作空间", role="admin", workspace_id="foreign-feedback",
                                    password_hash=PASSWORDS.hash("Fixture-Pass-2026!")))
                session.commit()
            login = client.post("/api/v1/auth/login", json={"username": username, "password": "Fixture-Pass-2026!"})
            assert login.status_code == 200
            client.headers["X-CSRF-Token"] = login.json()["csrf_token"]
            assert client.get(f"/api/v1/tickets/{chat['id']}").status_code == 404
    finally:
        settings.auth_enabled = original
