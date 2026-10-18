"""Deleting a customer's chat hides client access, never merchant/business evidence."""

from copy import deepcopy
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, inspect, select, text

from app.core.config import get_settings
from app.core.database import SessionLocal
from app.core.migrations import migrate_database
from app.main import app
from app.models import Account, Approval, AuditEvent, ModelUsage, QuestionRoute, Ticket, TicketAccess, ToolExecution, WorkflowCheckpoint
from app.services.auth import PASSWORDS


PASSWORD = "Fixture-Chat-Deletion-2026!"


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setattr(get_settings(), "auth_enabled", True)
    with TestClient(app, client=("delete-chat-" + uuid4().hex, 50000)) as client:
        yield client


def signup(client):
    username = "chat" + uuid4().hex[:12]
    result = client.post("/api/v1/auth/register", json={
        "username": username, "password": PASSWORD, "display_name": "聊天删除测试客户",
    })
    assert result.status_code == 201, result.text
    client.headers["X-CSRF-Token"] = result.json()["csrf_token"]
    return result.json()["user"]


def login(client, user):
    result = client.post("/api/v1/auth/login", json={"username": user["username"], "password": PASSWORD})
    assert result.status_code == 200, result.text
    client.headers["X-CSRF-Token"] = result.json()["csrf_token"]


def merchant(client, workspace="default"):
    with SessionLocal() as session:
        user = Account(username="merchant" + uuid4().hex[:10], display_name="记录保留测试管理员",
                       role="admin", workspace_id=workspace, password_hash=PASSWORDS.hash(PASSWORD))
        session.add(user)
        session.commit()
        identity = {"username": user.username}
    login(client, identity)
    return identity


def create_chat(client, message="您好", **extra):
    result = client.post("/api/v1/customer/tickets", json={"message": message, **extra})
    assert result.status_code == 201, result.text
    return result.json()


def business_snapshot(ticket_id):
    with SessionLocal() as session:
        ticket = session.get(Ticket, ticket_id)
        return {
            "ticket": {column.name: deepcopy(getattr(ticket, column.name)) for column in Ticket.__table__.columns},
            "related": {model.__tablename__: sorted(row.id for row in session.scalars(select(model).where(model.ticket_id == ticket_id)))
                        for model in [Approval, AuditEvent, QuestionRoute, ModelUsage, ToolExecution, WorkflowCheckpoint]},
        }


def test_delete_own_chat_is_persistent_idempotent_and_does_not_change_business_data(client):
    user = signup(client)
    first, second = create_chat(client), create_chat(client, "商城有哪些商品")
    snapshot = business_snapshot(first["id"])
    path = f"/api/v1/customer/tickets/{first['id']}"
    deleted = client.delete(path)
    assert deleted.status_code == 200 and deleted.json() == {"id": first["id"], "deleted": True}
    assert business_snapshot(first["id"]) == snapshot
    with SessionLocal() as session:
        deleted_at = session.get(TicketAccess, first["id"]).customer_deleted_at
        assert deleted_at is not None
    assert client.delete(path).status_code == 200
    with SessionLocal() as session:
        assert session.get(TicketAccess, first["id"]).customer_deleted_at == deleted_at
    assert [row["id"] for row in client.get("/api/v1/customer/tickets").json()] == [second["id"]]
    assert client.get(path).status_code == 404
    assert client.post(path + "/messages", json={"message": "不能复活已删除会话"}).status_code == 404
    assert client.post(path + "/confirmation", json={"resolved": True}).status_code == 404
    assert client.post("/api/v1/auth/logout").status_code == 200
    login(client, user)
    assert client.get(path).status_code == 404
    assert [row["id"] for row in client.get("/api/v1/customer/tickets").json()] == [second["id"]]
    assert business_snapshot(first["id"]) == snapshot
    third = create_chat(client, "你好")
    assert third["id"] != first["id"]


def test_deletion_keeps_merchant_conversation_feedback_routes_and_followup(client):
    customer = signup(client)
    chat = create_chat(client)
    ticket_id = chat["id"]
    confirmation = client.post(f"/api/v1/customer/tickets/{ticket_id}/confirmation",
                               json={"resolved": False, "rating": 3, "feedback": "希望进一步介绍商品"})
    assert confirmation.status_code == 200, confirmation.text
    admin = merchant(client)
    before = client.get(f"/api/v1/tickets/{ticket_id}").json()
    routes = [row for row in client.get("/api/v1/questions/routes").json() if row["ticket_id"] == ticket_id]
    login(client, customer)
    assert client.delete(f"/api/v1/customer/tickets/{ticket_id}").status_code == 200
    login(client, admin)
    after = client.get(f"/api/v1/tickets/{ticket_id}")
    assert after.status_code == 200 and after.json() == before
    assert ticket_id in {row["id"] for row in client.get("/api/v1/tickets").json()}
    assert [row for row in client.get("/api/v1/questions/routes").json() if row["ticket_id"] == ticket_id] == routes
    sent = client.post(f"/api/v1/tickets/{ticket_id}/draft/send", json={"response": "您好，您可以告诉我预算和想选购的商品类型，我会继续协助您选购。"})
    assert sent.status_code == 200, sent.text
    assert sent.json()["customer_feedbacks"] == before["customer_feedbacks"]
    assert sent.json()["conversation"][:-1] == before["conversation"]
    login(client, customer)
    assert client.get("/api/v1/customer/tickets").json() == []
    assert client.get(f"/api/v1/customer/tickets/{ticket_id}").status_code == 404


def test_deletion_preserves_order_payment_pending_approval_and_after_sales(client):
    merchant(client)
    product = client.post("/api/v1/products", json={"name": "保留记录测试商品", "description": "订单记录保留验证", "price": 50, "stock": 2}).json()
    signup(client)
    order = client.post("/api/v1/customer/orders", json={"product_id": product["id"], "quantity": 1,
                         "address": "测试地址", "idempotency_key": uuid4().hex}).json()
    paid = client.post(f"/api/v1/customer/orders/{order['id']}/action", json={"action": "pay"})
    assert paid.status_code == 200, paid.text
    chat = create_chat(client, "我要退款50元", order_id=order["order_no"], amount=50)
    snapshot = business_snapshot(chat["id"])
    orders_before = client.get("/api/v1/customer/orders").json()
    assert snapshot["ticket"]["requires_approval"] and snapshot["related"]["approvals"]
    assert client.delete(f"/api/v1/customer/tickets/{chat['id']}").status_code == 200
    assert business_snapshot(chat["id"]) == snapshot
    assert client.get("/api/v1/customer/orders").json() == orders_before
    merchant(client)
    assert chat["id"] in {row["id"] for row in client.get("/api/v1/tickets?case_type=after_sales").json()}
    assert client.get(f"/api/v1/tickets/{chat['id']}").json()["requires_approval"]


def test_delete_requires_session_csrf_and_owner_and_cannot_affect_other_workspace(client):
    owner = signup(client)
    chat = create_chat(client)
    path = f"/api/v1/customer/tickets/{chat['id']}"
    csrf = client.headers.pop("X-CSRF-Token")
    assert client.delete(path).status_code == 403
    client.headers["X-CSRF-Token"] = csrf
    client.post("/api/v1/auth/logout")
    assert client.delete(path).status_code == 401
    stranger = signup(client)
    assert client.delete(path).status_code == 404
    with SessionLocal() as session:
        assert session.get(TicketAccess, chat["id"]).customer_deleted_at is None
        # A same-owner identifier in the wrong workspace must still be denied.
        access = session.get(TicketAccess, chat["id"])
        access.owner_id = stranger["id"]
        access.workspace_id = "other-workspace"
        session.commit()
    assert client.delete(path).status_code == 404
    with SessionLocal() as session:
        access = session.get(TicketAccess, chat["id"])
        assert access.customer_deleted_at is None
        access.owner_id, access.workspace_id = owner["id"], "default"
        session.commit()
    login(client, owner)
    assert client.get(path).status_code == 200
    assert client.delete("/api/v1/customer/tickets/not-found").status_code == 404
    merchant(client)
    assert client.delete(path).status_code == 403
    # There is no merchant delete endpoint for business records.
    assert client.delete(f"/api/v1/tickets/{chat['id']}").status_code == 405
    assert client.get(f"/api/v1/tickets/{chat['id']}").status_code == 200


def test_visibility_migration_keeps_existing_owner_and_records_on_repeat_upgrade(tmp_path):
    engine = create_engine(f"sqlite:///{(tmp_path / 'visibility.db').as_posix()}")
    try:
        migrate_database(engine, "0003")
        with engine.begin() as connection:
            connection.execute(text("INSERT INTO accounts (id,username,display_name,password_hash,role,workspace_id,active,created_at) VALUES ('owner','test_owner','测试客户','unused-test-hash','customer','default',1,'2026-10-18')"))
            connection.execute(text("INSERT INTO tickets (id,ticket_no,customer_name,customer_tier,channel,subject,message,status,priority,sla_minutes,requires_approval,created_at,updated_at) VALUES ('keep','26-10-18-1','测试客户','standard','web','原会话','保留原始消息','new','medium',60,0,'2026-10-18','2026-10-18')"))
            connection.execute(text("INSERT INTO ticket_access (ticket_id,owner_id,workspace_id) VALUES ('keep','owner','default')"))
        migrate_database(engine)
        migrate_database(engine)
        assert "customer_deleted_at" in {column["name"] for column in inspect(engine).get_columns("ticket_access")}
        with engine.connect() as connection:
            assert connection.execute(text("SELECT owner_id,workspace_id,customer_deleted_at FROM ticket_access")).one() == ("owner", "default", None)
            assert connection.scalar(text("SELECT message FROM tickets WHERE id='keep'")) == "保留原始消息"
            assert connection.scalar(text("SELECT version_num FROM alembic_version")) == "0004"
    finally:
        engine.dispose()

