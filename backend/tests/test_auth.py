from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.core.config import get_settings
from app.core.database import SessionLocal
from app.main import app
from app.models import Account, TaskAccess
from app.services.auth import PASSWORDS


@pytest.fixture
def protected():
    settings = get_settings()
    original = settings.auth_enabled
    settings.auth_enabled = True
    with TestClient(app, client=("auth-" + uuid4().hex, 50000)) as client:
        yield client
    settings.auth_enabled = original


def signup(client, prefix="customer"):
    name = prefix + uuid4().hex[:10]
    response = client.post("/api/v1/auth/register", json={"username": name, "password": "Fixture-Pass-2026!", "display_name": "测试客户"})
    assert response.status_code == 201, response.text
    client.headers["X-CSRF-Token"] = response.json()["csrf_token"]
    return response.json()["user"]


def test_login_required_and_registration_cannot_promote_role(protected):
    assert protected.get("/api/v1/tickets").status_code == 401
    assert protected.get("/api/v1/health").status_code == 200
    user = signup(protected)
    assert user["role"] == "customer"
    assert protected.get("/api/v1/tickets").status_code == 403
    assert protected.post("/api/v1/tasks/evaluation", json={"limit": 3}).status_code == 403
    assert "HttpOnly" in protected.cookies.jar._cookies["testserver.local"]["/"]["resolveai_session"]._rest
    assert protected.get("/api/v1/auth/users").status_code == 403
    assert protected.post("/api/v1/auth/register", json={"username": "admin", "password": "Private-Fixture-Pass!", "display_name": "不允许占用后台账号"}).status_code == 409


def test_customer_ownership_csrf_and_logout(protected):
    signup(protected)
    created = protected.post("/api/v1/customer/tickets", json={"customer_name": "客户A", "subject": "查询物流", "message": "请查询物流进度"})
    assert created.status_code == 201, created.text
    ticket_id = created.json()["id"]
    assert protected.get(f"/api/v1/customer/tickets/{ticket_id}").status_code == 200
    csrf = protected.headers.pop("X-CSRF-Token")
    assert protected.post(f"/api/v1/customer/tickets/{ticket_id}/messages", json={"message": "补充信息"}).status_code == 403
    protected.headers["X-CSRF-Token"] = csrf
    assert protected.post("/api/v1/auth/logout").status_code == 200
    assert protected.get(f"/api/v1/customer/tickets/{ticket_id}").status_code == 401
    signup(protected, "other")
    assert protected.get(f"/api/v1/customer/tickets/{ticket_id}").status_code == 404
    assert protected.post(f"/api/v1/customer/tickets/{ticket_id}/messages", json={"message": "冒充他人"}).status_code == 404


def test_staff_roles_workspace_isolation_and_spoofed_reviewer(protected):
    suffix = uuid4().hex[:10]
    with SessionLocal() as session:
        for name, role, workspace in [("agent", "agent", "workspaceA"), ("review", "reviewer", "workspaceA"), ("foreign", "admin", "workspaceB")]:
            session.add(Account(username=name + suffix, display_name=name, role=role, workspace_id=workspace,
                                password_hash=PASSWORDS.hash("Fixture-Pass-2026!")))
        session.commit()
    def login(name):
        protected.cookies.clear()
        response = protected.post("/api/v1/auth/login", json={"username": name + suffix, "password": "Fixture-Pass-2026!"})
        assert response.status_code == 200
        protected.headers["X-CSRF-Token"] = response.json()["csrf_token"]
        return response.json()["user"]
    agent = login("agent")
    created = protected.post("/api/v1/tickets", json={"customer_name": "角色验收", "subject": "退款", "message": "申请退款180元", "order_id": "ORD-222", "amount": 180})
    assert created.status_code == 201, created.text
    ticket_id = created.json()["id"]
    assert protected.post(f"/api/v1/tickets/{ticket_id}/decision", json={"decision": "approve", "reviewer": "假冒管理员"}).status_code == 403
    analyzed = protected.post(f"/api/v1/tickets/{ticket_id}/analyze")
    assert analyzed.status_code == 200
    with SessionLocal() as session:
        session.add(TaskAccess(task_id="scope-" + suffix, owner_id=agent["id"], workspace_id="workspaceA"))
        session.commit()
    login("foreign")
    assert protected.get(f"/api/v1/tickets/{ticket_id}").status_code == 404
    assert all(ticket["id"] != ticket_id for ticket in protected.get("/api/v1/tickets").json())
    assert protected.get("/api/v1/dashboard").json()["metrics"][0]["value"] == "0"
    login("review")
    reviewed = protected.post(f"/api/v1/tickets/{ticket_id}/decision", json={"decision": "approve", "reviewer": "假冒管理员"})
    assert reviewed.status_code == 200, reviewed.text
    assert reviewed.json()["approval"]["reviewed_by"] == "review" + suffix


def test_wrong_origin_and_weak_password_rejected(protected):
    assert protected.post("/api/v1/auth/register", headers={"Origin": "https://attacker.invalid"}, json={"username": "weakuser", "password": "Short!", "display_name": "拒绝"}).status_code == 403
    assert protected.post("/api/v1/auth/register", json={"username": "weakuser", "password": "Short!", "display_name": "拒绝"}).status_code == 422


def test_login_throttling_and_validation_does_not_echo_password(protected):
    value = "private-input-" * 20
    response = protected.post("/api/v1/auth/login", json={"username": "nobody", "password": value})
    assert response.status_code == 422 and value not in response.text
    for _ in range(10):
        assert protected.post("/api/v1/auth/login", json={"username": "nobody", "password": "wrong-password"}).status_code == 401
    assert protected.post("/api/v1/auth/login", json={"username": "nobody", "password": "wrong-password"}).status_code == 429


def test_password_change_revokes_old_session(protected):
    user = signup(protected)
    old_token = protected.cookies.get("resolveai_session")
    response = protected.post("/api/v1/auth/password", json={"current_password": "Fixture-Pass-2026!", "new_password": "Updated-Private-Pass!"})
    assert response.status_code == 200
    assert protected.get("/api/v1/auth/me").status_code == 200
    protected.cookies.clear(); protected.cookies.set("resolveai_session", old_token)
    assert protected.get("/api/v1/auth/me").status_code == 401


def test_private_knowledge_task_and_admin_account_changes(protected):
    from app.api.routes import get_task_queue_service
    from app.models import KnowledgeDocument, KnowledgeAccess
    from tests.test_tasks import FakeTaskQueue
    suffix = uuid4().hex[:10]
    with SessionLocal() as session:
        first = Account(username="adm" + suffix, display_name="A", role="admin", workspace_id="private-A", password_hash=PASSWORDS.hash("Fixture-Pass-2026!"))
        second = Account(username="oth" + suffix, display_name="B", role="admin", workspace_id="private-B", password_hash=PASSWORDS.hash("Fixture-Pass-2026!"))
        session.add_all([first, second]); session.commit()
        document = KnowledgeDocument(title="私有物流约定", category="logistics", source="private://"+suffix, content="私有物流约定", embedding=[1.0]*256)
        session.add(document); session.commit()
        document_id = document.id
        session.add(KnowledgeAccess(document_id=document.id, workspace_id=first.workspace_id))
        session.add(TaskAccess(task_id="private-task-" + suffix, owner_id=first.id, workspace_id=first.workspace_id)); session.commit()
    app.dependency_overrides[get_task_queue_service] = lambda: FakeTaskQueue()
    try:
        def login(prefix):
            protected.cookies.clear()
            response = protected.post("/api/v1/auth/login", json={"username": prefix+suffix, "password": "Fixture-Pass-2026!"})
            assert response.status_code == 200
            protected.headers["X-CSRF-Token"] = response.json()["csrf_token"]
            return response.json()["user"]
        login("oth")
        assert protected.get(f"/api/v1/knowledge/documents/{document_id}").status_code == 404
        assert protected.get(f"/api/v1/tasks/private-task-{suffix}").status_code == 404
        assert protected.get(f"/api/v1/tasks/private-task-{suffix}/events").status_code == 404
        own = login("adm")
        assert protected.get(f"/api/v1/knowledge/documents/{document_id}").status_code == 200
        assert protected.get(f"/api/v1/tasks/private-task-{suffix}/events").status_code == 200
        created = protected.post("/api/v1/auth/users", json={"username": "staff"+suffix, "display_name": "新坐席", "password": "Fixture-Pass-2026!", "role": "agent"})
        assert created.status_code == 201
        assert protected.patch(f"/api/v1/auth/users/{own['id']}", json={"active": False}).status_code == 409
        assert protected.patch(f"/api/v1/auth/users/{created.json()['id']}", json={"active": False}).status_code == 200
    finally:
        app.dependency_overrides.pop(get_task_queue_service, None)
