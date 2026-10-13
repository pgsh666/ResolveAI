"""Server-side identity, object ownership and role checks; never trust model/user role claims."""

import re
import secrets
from contextvars import ContextVar
from dataclasses import dataclass
from datetime import timedelta, timezone
from hashlib import sha256

from fastapi import HTTPException, Request
from pwdlib import PasswordHash
from sqlalchemy import and_, func, or_, select

from app.core.config import get_settings
from app.models import Account, KnowledgeAccess, KnowledgeDocument, LoginAttempt, LoginSession, TaskAccess, Ticket, TicketAccess, utc_now

PASSWORDS = PasswordHash.recommended()
DUMMY_HASH = PASSWORDS.hash(secrets.token_urlsafe(24))
ROLES = {"customer", "agent", "reviewer", "admin"}
COOKIE_NAME = "resolveai_session"


@dataclass(frozen=True)
class Actor:
    id: str
    username: str
    display_name: str
    role: str
    workspace_id: str


_actor: ContextVar[Actor | None] = ContextVar("resolveai_actor", default=None)


def current_actor() -> Actor:
    if not get_settings().auth_enabled:
        return Actor("isolated-test", "test", "测试管理员", "admin", "default")
    result = _actor.get()
    if result is None:
        raise HTTPException(401, "请先登录")
    return result


def require_role(*roles):
    actor = current_actor()
    if actor.role not in roles:
        raise HTTPException(403, "当前账号没有此操作权限")
    return actor


def normalize_username(value):
    result = value.strip().lower()
    if not re.fullmatch(r"[a-z0-9][a-z0-9_.-]{1,79}", result):
        raise HTTPException(422, "用户名请使用2～80位英文、数字、点、横线或下划线")
    return result


def validate_password(value):
    if not 12 <= len(value) <= 128 or len(value.encode()) > 512:
        raise HTTPException(422, "密码长度需为12～128个字符")


def ensure_bootstrap_accounts(session):
    for item in get_settings().auth_bootstrap_users:
        username = normalize_username(item.get("username", ""))
        if item.get("role") not in {"admin", "agent", "reviewer"}:
            raise ValueError("Bootstrap accounts must have a staff role")
        validate_password(item.get("password", ""))
        existing = session.scalar(select(Account).where(Account.username == username))
        if existing and existing.role == "customer":
            raise ValueError("A bootstrap staff name is held by a customer; refusing automatic promotion")
        if existing:
            continue
        session.add(Account(username=username, display_name=item.get("display_name", username),
                            password_hash=PASSWORDS.hash(item["password"]), role=item["role"], workspace_id="default"))
    session.commit()


def actor_from_account(account):
    return Actor(account.id, account.username, account.display_name, account.role, account.workspace_id)


def authenticate(request: Request, session):
    if not get_settings().auth_enabled:
        return current_actor(), None
    raw = request.cookies.get(COOKIE_NAME, "")
    if not 20 <= len(raw) <= 128:
        raise HTTPException(401, "请先登录")
    record = session.get(LoginSession, sha256(raw.encode()).hexdigest())
    if not record or record.expires_at.replace(tzinfo=timezone.utc) <= utc_now():
        raise HTTPException(401, "登录已过期，请重新登录")
    account = session.get(Account, record.account_id)
    if not account or not account.active or account.role not in ROLES:
        raise HTTPException(401, "登录已失效")
    return actor_from_account(account), record


def check_origin(request):
    origin = request.headers.get("origin")
    if origin and origin not in get_settings().cors_origin_list:
        raise HTTPException(403, "请求来源不受信任")


def check_csrf(request, record):
    if not get_settings().auth_enabled or request.method in {"GET", "HEAD", "OPTIONS"}:
        return
    check_origin(request)
    if not record or not secrets.compare_digest(request.headers.get("x-csrf-token", ""), record.csrf_token):
        raise HTTPException(403, "请求校验失败，请刷新登录状态后重试")


async def authorize_request(request: Request):
    from app.core.database import SessionLocal
    suffix = request.url.path.removeprefix(get_settings().api_prefix)
    if suffix == "/health":
        yield
        return
    with SessionLocal() as session:
        actor, record = authenticate(request, session)
        check_csrf(request, record)
    if not suffix.startswith("/customer/") and actor.role == "customer":
        raise HTTPException(403, "客户账号不能访问客服后台")
    if request.method not in {"GET", "HEAD", "OPTIONS"} and not suffix.startswith("/customer/"):
        if suffix.endswith("/decision") or re.fullmatch(r"/tickets/[^/]+/tools/[^/]+/execute", suffix):
            allowed = {"reviewer", "admin"}
        elif suffix.startswith("/tasks/knowledge-import") or suffix.startswith("/tasks/evaluation"):
            allowed = {"admin"}
        else:
            allowed = {"agent", "admin"}
        if actor.role not in allowed:
            raise HTTPException(403, "当前账号没有此操作权限")
    if suffix in {"/performance/metrics", "/observability"} and actor.role != "admin":
        raise HTTPException(403, "此信息仅管理员可查看")
    token = _actor.set(actor)
    try:
        yield
    finally:
        _actor.reset(token)


def ticket_scope():
    if not get_settings().auth_enabled:
        return True
    actor = current_actor()
    access = select(TicketAccess.ticket_id).where(TicketAccess.workspace_id == actor.workspace_id)
    if actor.role == "customer":
        access = access.where(TicketAccess.owner_id == actor.id)
        return Ticket.id.in_(access)
    # Legacy records are retained, assigned only to the default project staff.
    legacy = ~Ticket.id.in_(select(TicketAccess.ticket_id)) if actor.workspace_id == "default" else False
    return or_(Ticket.id.in_(access), legacy)


def knowledge_scope(workspace_id="default"):
    return or_(~KnowledgeDocument.id.in_(select(KnowledgeAccess.document_id)), KnowledgeDocument.id.in_(
        select(KnowledgeAccess.document_id).where(KnowledgeAccess.workspace_id == workspace_id)))


def assert_ticket_access(ticket_id, session, *, customer_action=False):
    if not get_settings().auth_enabled:
        return
    actor = current_actor()
    access = session.get(TicketAccess, ticket_id)
    allowed = bool(access and access.workspace_id == actor.workspace_id and
                   (actor.role != "customer" or access.owner_id == actor.id))
    if access is None and actor.role != "customer" and actor.workspace_id == "default" and not customer_action:
        allowed = True
    if customer_action:
        allowed = bool(access and access.workspace_id == actor.workspace_id and access.owner_id == actor.id)
    if not allowed:
        raise HTTPException(404, "工单不存在")


def attach_ticket_access(ticket, session):
    if get_settings().auth_enabled:
        actor = current_actor()
        session.flush()
        session.add(TicketAccess(ticket_id=ticket.id, owner_id=actor.id, workspace_id=actor.workspace_id))


def attach_task_access(task_id, session):
    if get_settings().auth_enabled:
        actor = current_actor()
        session.add(TaskAccess(task_id=task_id, owner_id=actor.id, workspace_id=actor.workspace_id))
        session.commit()


def assert_task_access(task_id, session):
    if not get_settings().auth_enabled:
        return
    actor = require_role("admin", "agent", "reviewer")
    access = session.get(TaskAccess, task_id)
    if not access or access.workspace_id != actor.workspace_id:
        raise HTTPException(404, "后台任务不存在")


def check_login_rate(request, session):
    identity = sha256((request.client.host if request.client else "unknown").encode()).hexdigest()
    count = session.scalar(select(func.count(LoginAttempt.id)).where(and_(
        LoginAttempt.identity_hash == identity, LoginAttempt.created_at >= utc_now() - timedelta(minutes=10)))) or 0
    if count >= 10:
        raise HTTPException(429, "登录尝试过多，请10分钟后重试", headers={"Retry-After": "600"})
    return identity
