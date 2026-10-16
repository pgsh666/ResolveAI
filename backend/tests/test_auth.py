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


def test_registration_can_require_explicit_login_before_chat(protected):
    username = "onboard" + uuid4().hex[:10]
    password = "Fixture-Onboarding-2026!"
    response = protected.post("/api/v1/auth/register", json={"username":username, "password":password,
                             "display_name":"注册用户", "auto_login":False})
    assert response.status_code == 201
    assert response.json()["csrf_token"] == ""
    assert protected.get("/api/v1/auth/me").status_code == 401
    login = protected.post("/api/v1/auth/login", json={"username":username, "password":password})
    assert login.status_code == 200
    protected.headers["X-CSRF-Token"] = login.json()["csrf_token"]
    first = protected.post("/api/v1/customer/tickets", json={"message":"您好"})
    assert first.status_code == 201, first.text
    assert first.json()["customer_name"] == "注册用户" and first.json()["handed_off"] is False
    assert first.json()["messages"][-1]["role"] == "support"


@pytest.mark.parametrize("role", ["admin"])
def test_staff_accounts_cannot_read_or_act_as_customer(protected, role):
    username = "portal" + role + uuid4().hex[:8]
    with SessionLocal() as session:
        session.add(Account(username=username, display_name="工作人员", role=role,
                            password_hash=PASSWORDS.hash("Fixture-Pass-2026!"), workspace_id="default"))
        session.commit()
    response = protected.post("/api/v1/auth/login", json={"username":username, "password":"Fixture-Pass-2026!"})
    assert response.status_code == 200
    protected.headers["X-CSRF-Token"] = response.json()["csrf_token"]
    for path in ["/customer/products", "/customer/orders", "/customer/tickets", "/customer/tickets/not-a-ticket"]:
        denied = protected.get("/api/v1" + path)
        assert denied.status_code == 403, (role, path, denied.text)
        assert "仅限客户账号" in denied.json()["detail"]
    for path, payload in [
        ("/customer/tickets", {"message":"帮我找蓝牙耳机"}),
        ("/customer/tickets/not-a-ticket/messages", {"message":"工作人员不能模拟客户"}),
        ("/customer/tickets/not-a-ticket/confirmation", {"resolved":True}),
        ("/customer/orders", {"product_id":"not-a-product", "quantity":1, "address":"测试地址", "idempotency_key":uuid4().hex}),
        ("/customer/orders/not-an-order/action", {"action":"pay"}),
    ]:
        denied = protected.post("/api/v1" + path, json=payload)
        assert denied.status_code == 403, (role, path, denied.text)
    # Role separation does not remove staff's legitimate merchant visibility.
    assert protected.get("/api/v1/products").status_code == 200
    assert protected.get("/api/v1/orders").status_code == 200


def test_store_ownership_and_customer_cannot_manage_catalog_or_logistics(protected):
    signup(protected)
    products = protected.get("/api/v1/customer/products").json()
    product = next(p for p in products if p["stock"] > 0)
    assert protected.post("/api/v1/products", json={"name":"越权上架", "description":"客户不能上架商品", "price":10, "stock":1}).status_code == 403
    assert protected.get("/api/v1/orders").status_code == 403
    response = protected.post("/api/v1/customer/orders", json={"product_id":product["id"], "quantity":1, "address":"测试地址", "idempotency_key":uuid4().hex})
    assert response.status_code == 201, response.text
    order = response.json()
    assert protected.post(f"/api/v1/customer/orders/{order['id']}/action", json={"action":"ship"}).status_code == 403
    protected.post("/api/v1/auth/logout")
    signup(protected, "other-shop")
    assert protected.get("/api/v1/customer/orders").json() == []
    assert protected.post(f"/api/v1/customer/orders/{order['id']}/action", json={"action":"pay"}).status_code == 404
    ticket = protected.post("/api/v1/customer/tickets", json={"customer_name":"客户B", "subject":"查询物流", "message":"查询这个订单的物流状态", "order_id":order["order_no"]}).json()
    assert "请您提供订单编号" in ticket["messages"][-1]["content"]
    assert "已核验" not in ticket["messages"][-1]["content"]


def test_admin_workspace_isolation_and_authenticated_approval_identity(protected):
    suffix = uuid4().hex[:10]
    with SessionLocal() as session:
        for name, role, workspace in [("operator", "admin", "workspaceA"), ("review", "admin", "workspaceA"), ("foreign", "admin", "workspaceB")]:
            session.add(Account(username=name + suffix, display_name=name, role=role, workspace_id=workspace,
                                password_hash=PASSWORDS.hash("Fixture-Pass-2026!")))
        session.commit()
    def login(name):
        protected.cookies.clear()
        response = protected.post("/api/v1/auth/login", json={"username": name + suffix, "password": "Fixture-Pass-2026!"})
        assert response.status_code == 200
        protected.headers["X-CSRF-Token"] = response.json()["csrf_token"]
        return response.json()["user"]
    operator = login("operator")
    created = protected.post("/api/v1/tickets", json={"customer_name": "角色验收", "subject": "退款", "message": "申请退款180元", "order_id": "ORD-222", "amount": 180})
    assert created.status_code == 201, created.text
    ticket_id = created.json()["id"]
    analyzed = protected.post(f"/api/v1/tickets/{ticket_id}/analyze")
    assert analyzed.status_code == 200
    with SessionLocal() as session:
        session.add(TaskAccess(task_id="scope-" + suffix, owner_id=operator["id"], workspace_id="workspaceA"))
        session.commit()
    login("foreign")
    assert protected.get(f"/api/v1/tickets/{ticket_id}").status_code == 404
    assert all(ticket["id"] != ticket_id for ticket in protected.get("/api/v1/tickets").json())
    assert protected.get("/api/v1/dashboard").json()["metrics"][0]["value"] == "0"
    login("review")
    reviewed = protected.post(f"/api/v1/tickets/{ticket_id}/decision", json={"decision": "approve", "reviewer": "假冒管理员"})
    assert reviewed.status_code == 200, reviewed.text
    assert reviewed.json()["approval"]["reviewed_by"] == "review" + suffix


def test_wrong_origin_and_empty_password_rejected(protected):
    assert protected.post("/api/v1/auth/register", headers={"Origin": "https://attacker.invalid"}, json={"username": "weakuser", "password": "Short!", "display_name": "拒绝"}).status_code == 403
    assert protected.post("/api/v1/auth/register", json={"username": "blankuser", "password": "", "display_name": "拒绝"}).status_code == 422


@pytest.mark.parametrize("password", ["1", "123456", "abc", "中文密码", "🔒", " a "])
def test_customer_registration_accepts_short_passwords_without_format_rules(protected, password):
    username = "simple" + uuid4().hex[:10]
    response = protected.post("/api/v1/auth/register", json={"username":username, "password":password,
                             "display_name":"注册客户", "auto_login":False})
    assert response.status_code == 201, response.text
    assert response.json()["user"]["role"] == "customer"
    assert protected.get("/api/v1/auth/me").status_code == 401
    with SessionLocal() as session:
        account = session.scalar(select(Account).where(Account.username == username))
        assert account.password_hash != password and PASSWORDS.verify(password, account.password_hash)
    login = protected.post("/api/v1/auth/login", json={"username":username, "password":password})
    assert login.status_code == 200 and login.json()["user"]["role"] == "customer"


@pytest.mark.parametrize("password", ["", "   ", "a" * 129])
def test_customer_registration_still_rejects_blank_and_oversized_passwords(protected, password):
    response = protected.post("/api/v1/auth/register", json={"username":"invalid"+uuid4().hex[:8],
                             "password":password, "display_name":"拒绝"})
    assert response.status_code == 422


def test_staff_password_policy_is_not_relaxed_by_customer_signup_change():
    from fastapi import HTTPException
    from app.services.auth import validate_password
    with pytest.raises(HTTPException) as error:
        validate_password("1")
    assert error.value.status_code == 422


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
        assert created.status_code == 403
        assert protected.patch(f"/api/v1/auth/users/{own['id']}", json={"active": False}).status_code == 403
        assert protected.patch(f"/api/v1/auth/users/{own['id']}", json={"display_name": "自己的新名称"}).status_code == 200
        assert protected.get("/api/v1/auth/users").json()[0]["id"] == own["id"]
    finally:
        app.dependency_overrides.pop(get_task_queue_service, None)


@pytest.mark.parametrize("role", ["agent", "reviewer"])
def test_retired_staff_roles_cannot_login_or_reuse_old_session(protected, role):
    from datetime import timedelta
    from hashlib import sha256
    from app.models import LoginSession, utc_now
    username = "retired" + uuid4().hex[:10]
    old_token = uuid4().hex + uuid4().hex
    with SessionLocal() as session:
        account = Account(username=username, display_name="历史工作人员", role=role,
                          password_hash=PASSWORDS.hash("Fixture-Pass-2026!"), workspace_id="default")
        session.add(account)
        session.flush()
        session.add(LoginSession(token_hash=sha256(old_token.encode()).hexdigest(), account_id=account.id,
                                 csrf_token=uuid4().hex, expires_at=utc_now() + timedelta(hours=1)))
        session.commit()
    login = protected.post("/api/v1/auth/login", json={"username":username, "password":"Fixture-Pass-2026!"})
    assert login.status_code == 403 and "停用" in login.json()["detail"]
    assert "resolveai_session" not in protected.cookies
    protected.cookies.set("resolveai_session", old_token)
    assert protected.get("/api/v1/auth/me").status_code == 401
    assert protected.get("/api/v1/tickets").status_code == 401
    with SessionLocal() as session:
        preserved = session.scalar(select(Account).where(Account.username == username))
        assert preserved is not None and preserved.role == role


def test_admin_only_can_edit_own_profile_never_other_accounts(protected):
    suffix = uuid4().hex[:10]
    with SessionLocal() as session:
        accounts = [Account(username=name+suffix, display_name=name, role=role, workspace_id="default",
                            password_hash=PASSWORDS.hash("Fixture-Pass-2026!"))
                    for name, role in [("self", "admin"), ("customer", "customer"), ("other", "admin")]]
        session.add_all(accounts)
        session.commit()
        own_id, customer_id, other_id = [account.id for account in accounts]
        originals = {account.id: (account.display_name, account.role, account.active, account.password_hash) for account in accounts}
    response = protected.post("/api/v1/auth/login", json={"username":"self"+suffix, "password":"Fixture-Pass-2026!"})
    assert response.status_code == 200
    protected.headers["X-CSRF-Token"] = response.json()["csrf_token"]
    assert [a["id"] for a in protected.get("/api/v1/auth/users").json()] == [own_id]
    for role in ["customer", "admin", "agent", "reviewer"]:
        assert protected.post("/api/v1/auth/users", json={"username":"new"+suffix, "role":role,
                              "display_name":"不能创建", "password":"Fixture-Pass-2026!"}).status_code == 403
    for target in [customer_id, other_id, "unknown-id"]:
        for payload in [{"display_name":"篡改"}, {"active":False}, {"role":"admin"}]:
            assert protected.patch(f"/api/v1/auth/users/{target}", json=payload).status_code == 403
    for payload in [{"role":"customer"}, {"active":False}, {"role":"admin", "display_name":"不应生效"}]:
        assert protected.patch(f"/api/v1/auth/users/{own_id}", json=payload).status_code == 403
    for payload in [{"username":"newname"}, {"password_hash":"plaintext"}, {"password":"newpassword"}]:
        assert protected.patch(f"/api/v1/auth/users/{own_id}", json=payload).status_code == 422
    assert protected.patch(f"/api/v1/auth/users/{own_id}", json={"display_name":"   "}).status_code == 422
    with SessionLocal() as session:
        for identifier, values in originals.items():
            account = session.get(Account, identifier)
            assert (account.display_name, account.role, account.active, account.password_hash) == values
    updated = protected.patch(f"/api/v1/auth/users/{own_id}", json={"display_name":"  自己的新名称  "})
    assert updated.status_code == 200 and updated.json()["display_name"] == "自己的新名称"
    assert protected.get("/api/v1/auth/me").json()["user"]["display_name"] == "自己的新名称"
    changed = protected.post("/api/v1/auth/password", json={"current_password":"Fixture-Pass-2026!", "new_password":"Updated-Private-Pass!"})
    assert changed.status_code == 200
    with SessionLocal() as session:
        assert PASSWORDS.verify("Updated-Private-Pass!", session.get(Account, own_id).password_hash)
        for identifier in [customer_id, other_id]:
            account = session.get(Account, identifier)
            assert (account.display_name, account.role, account.active, account.password_hash) == originals[identifier]


def test_bootstrap_creates_admin_only_and_does_not_promote_retired_accounts():
    from app.services.auth import ensure_bootstrap_accounts
    settings = get_settings()
    original = settings.auth_bootstrap_users
    suffix = uuid4().hex[:10]
    with SessionLocal() as session:
        retired = Account(username="old"+suffix, display_name="旧账号", role="agent", workspace_id="default",
                          password_hash=PASSWORDS.hash("Fixture-Pass-2026!"))
        session.add(retired)
        session.commit()
        try:
            settings.auth_bootstrap_users = [{"username":name+suffix, "display_name":name, "role":role,
                                             "password":"Fixture-Pass-2026!"}
                                            for name, role in [("admin", "admin"), ("skipagent", "agent"), ("skipreview", "reviewer"), ("old", "admin")]]
            ensure_bootstrap_accounts(session)
            assert session.scalar(select(Account).where(Account.username == "admin"+suffix)).role == "admin"
            assert session.scalar(select(Account).where(Account.username == "skipagent"+suffix)) is None
            assert session.scalar(select(Account).where(Account.username == "skipreview"+suffix)) is None
            assert session.get(Account, retired.id).role == "agent"
        finally:
            settings.auth_bootstrap_users = original
