import secrets
from datetime import timedelta
from hashlib import sha256
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from pydantic import BaseModel, Field
from sqlalchemy import delete, func, select
from sqlalchemy.exc import IntegrityError

from app.core.config import get_settings
from app.core.database import get_db
from app.models import Account, LoginAttempt, LoginSession, utc_now
from app.services.auth import (COOKIE_NAME, PASSWORDS, DUMMY_HASH, authenticate, check_csrf, check_login_rate,
                               check_origin, normalize_username, validate_password)
from app.services.security import redact_text

router = APIRouter(prefix="/auth", tags=["accounts"])


class Credentials(BaseModel):
    username: str = Field(min_length=2, max_length=80)
    password: str = Field(min_length=1, max_length=128, repr=False)


class Registration(Credentials):
    display_name: str = Field(min_length=1, max_length=100)


class StaffCreate(Registration):
    role: Literal["customer", "agent", "reviewer", "admin"]


class AccountUpdate(BaseModel):
    role: Literal["customer", "agent", "reviewer", "admin"] | None = None
    active: bool | None = None


class PasswordChange(BaseModel):
    current_password: str = Field(min_length=1, max_length=128, repr=False)
    new_password: str = Field(min_length=12, max_length=128, repr=False)


@router.post("/password")
def change_password(payload: PasswordChange, request: Request, response: Response, session=Depends(get_db)):
    actor, record = authenticate(request, session)
    check_csrf(request, record)
    account = session.get(Account, actor.id)
    identity = check_login_rate(request, session)
    if not account or not PASSWORDS.verify(payload.current_password, account.password_hash):
        session.add(LoginAttempt(identity_hash=identity))
        session.commit()
        raise HTTPException(401, "当前密码错误")
    validate_password(payload.new_password)
    account.password_hash = PASSWORDS.hash(payload.new_password)
    session.execute(delete(LoginSession).where(LoginSession.account_id == actor.id))
    session.commit()
    return start_session(account, response, session)


def account_view(account):
    return {"id": account.id, "username": account.username, "display_name": account.display_name,
            "role": account.role, "workspace_id": account.workspace_id, "active": account.active}


def start_session(account, response, session):
    raw = secrets.token_urlsafe(32)
    record = LoginSession(token_hash=sha256(raw.encode()).hexdigest(), account_id=account.id,
                          csrf_token=secrets.token_urlsafe(32),
                          expires_at=utc_now() + timedelta(hours=get_settings().auth_session_hours))
    session.add(record)
    session.commit()
    response.set_cookie(COOKIE_NAME, raw, httponly=True, secure=get_settings().auth_cookie_secure,
                        samesite="lax", max_age=get_settings().auth_session_hours * 3600, path="/")
    return {"user": account_view(account), "csrf_token": record.csrf_token}


@router.get("/status")
def status(session=Depends(get_db)):
    return {"auth_enabled": get_settings().auth_enabled, "staff_initialized": bool(session.scalar(
        select(Account.id).where(Account.role == "admin", Account.active.is_(True)).limit(1)))}


@router.post("/register", status_code=201)
def register(payload: Registration, request: Request, response: Response, session=Depends(get_db)):
    check_origin(request)
    identity = check_login_rate(request, session)
    session.add(LoginAttempt(identity_hash=identity))
    session.commit()
    validate_password(payload.password)
    username = normalize_username(payload.username)
    reserved = {"admin", "agent", "reviewer"} | {item.get("username", "").lower() for item in get_settings().auth_bootstrap_users}
    if username in reserved:
        raise HTTPException(409, "该用户名保留给后台账号，请选择其他用户名")
    account = Account(username=username, display_name=redact_text(payload.display_name),
                      password_hash=PASSWORDS.hash(payload.password), role="customer", workspace_id="default")
    session.add(account)
    try:
        session.commit()
    except IntegrityError as error:
        session.rollback()
        raise HTTPException(409, "用户名已存在") from error
    return start_session(account, response, session)


@router.post("/login")
def login(payload: Credentials, request: Request, response: Response, session=Depends(get_db)):
    check_origin(request)
    identity = check_login_rate(request, session)
    account = session.scalar(select(Account).where(Account.username == normalize_username(payload.username)))
    verified = PASSWORDS.verify(payload.password, account.password_hash if account else DUMMY_HASH)
    if not account or not verified or not account.active:
        session.add(LoginAttempt(identity_hash=identity))
        session.commit()
        raise HTTPException(401, "账号或密码错误")
    return start_session(account, response, session)


@router.get("/me")
def me(request: Request, session=Depends(get_db)):
    actor, record = authenticate(request, session)
    return {"user": {**actor.__dict__, "active": True}, "csrf_token": record.csrf_token if record else "isolated-test"}


@router.post("/logout")
def logout(request: Request, response: Response, session=Depends(get_db)):
    _, record = authenticate(request, session)
    check_csrf(request, record)
    if record:
        session.delete(record)
        session.commit()
    response.delete_cookie(COOKIE_NAME, path="/", secure=get_settings().auth_cookie_secure, httponly=True, samesite="lax")
    return {"logged_out": True}


def admin(request, session):
    actor, record = authenticate(request, session)
    check_csrf(request, record)
    if actor.role != "admin":
        raise HTTPException(403, "仅管理员可管理账号")
    return actor


@router.get("/users")
def users(request: Request, session=Depends(get_db)):
    actor = admin(request, session)
    return [account_view(user) for user in session.scalars(select(Account).where(Account.workspace_id == actor.workspace_id)).all()]


@router.post("/users", status_code=201)
def create_user(payload: StaffCreate, request: Request, session=Depends(get_db)):
    actor = admin(request, session)
    validate_password(payload.password)
    account = Account(username=normalize_username(payload.username), display_name=redact_text(payload.display_name),
                      password_hash=PASSWORDS.hash(payload.password), role=payload.role, workspace_id=actor.workspace_id)
    session.add(account)
    try:
        session.commit()
    except IntegrityError as error:
        session.rollback()
        raise HTTPException(409, "用户名已存在") from error
    return account_view(account)


@router.patch("/users/{user_id}")
def update_user(user_id: str, payload: AccountUpdate, request: Request, session=Depends(get_db)):
    actor = admin(request, session)
    account = session.get(Account, user_id)
    if not account or account.workspace_id != actor.workspace_id:
        raise HTTPException(404, "账号不存在")
    if user_id == actor.id and (payload.active is False or payload.role and payload.role != "admin"):
        raise HTTPException(409, "不能停用或降级当前管理员账号")
    if payload.role:
        account.role = payload.role
    if payload.active is not None:
        account.active = payload.active
    session.execute(delete(LoginSession).where(LoginSession.account_id == user_id))
    session.commit()
    return account_view(account)
