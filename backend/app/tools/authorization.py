import base64
import hashlib
import hmac
import json
import time
from typing import Any


class ApprovalTokenError(ValueError):
    pass


def canonical_argument_hash(arguments: dict[str, Any]) -> str:
    payload = json.dumps(
        arguments,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def create_approval_token(
    *,
    secret: str,
    ticket_id: str,
    approval_id: str,
    tool_name: str,
    idempotency_key: str,
    arguments: dict[str, Any],
    ttl_seconds: int = 300,
) -> str:
    if not 1 <= ttl_seconds <= 600:
        raise ApprovalTokenError("审批凭证有效期必须在 1 至 600 秒之间")
    issued_at = int(time.time())
    claims = {
        "ticket_id": ticket_id,
        "approval_id": approval_id,
        "tool_name": tool_name,
        "idempotency_key": idempotency_key,
        "arguments_sha256": canonical_argument_hash(arguments),
        "issued_at": issued_at,
        "expires_at": issued_at + ttl_seconds,
    }
    encoded_claims = _base64_encode(
        json.dumps(claims, sort_keys=True, separators=(",", ":")).encode("utf-8")
    )
    signature = hmac.new(secret.encode("utf-8"), encoded_claims.encode("ascii"), hashlib.sha256)
    return f"{encoded_claims}.{signature.hexdigest()}"


def verify_approval_token(
    token: str,
    *,
    secret: str,
    tool_name: str,
    idempotency_key: str,
    arguments: dict[str, Any],
    expected_ticket_id: str | None = None,
    expected_approval_id: str | None = None,
) -> dict[str, Any]:
    try:
        encoded_claims, provided_signature = token.split(".", 1)
    except ValueError as error:
        raise ApprovalTokenError("审批凭证格式无效") from error

    expected_signature = hmac.new(
        secret.encode("utf-8"), encoded_claims.encode("ascii"), hashlib.sha256
    ).hexdigest()
    if not hmac.compare_digest(provided_signature, expected_signature):
        raise ApprovalTokenError("审批凭证签名无效")

    try:
        claims = json.loads(_base64_decode(encoded_claims))
    except (ValueError, json.JSONDecodeError) as error:
        raise ApprovalTokenError("审批凭证内容无效") from error

    now = int(time.time())
    required_claims = {
        "ticket_id",
        "approval_id",
        "tool_name",
        "idempotency_key",
        "arguments_sha256",
        "issued_at",
        "expires_at",
    }
    if not required_claims.issubset(claims):
        raise ApprovalTokenError("审批凭证缺少必要字段")
    if claims.get("issued_at", now + 1) > now + 30:
        raise ApprovalTokenError("审批凭证签发时间无效")
    if claims.get("expires_at", 0) - claims.get("issued_at", 0) > 600:
        raise ApprovalTokenError("审批凭证有效期超过安全上限")
    if claims.get("expires_at", 0) < now:
        raise ApprovalTokenError("审批凭证已过期")
    if claims.get("tool_name") != tool_name:
        raise ApprovalTokenError("审批凭证与工具不匹配")
    if claims.get("idempotency_key") != idempotency_key:
        raise ApprovalTokenError("审批凭证与幂等键不匹配")
    if claims.get("arguments_sha256") != canonical_argument_hash(arguments):
        raise ApprovalTokenError("审批凭证与工具参数不匹配")
    if expected_ticket_id and claims.get("ticket_id") != expected_ticket_id:
        raise ApprovalTokenError("审批凭证与工单不匹配")
    if expected_approval_id and claims.get("approval_id") != expected_approval_id:
        raise ApprovalTokenError("审批凭证与审批记录不匹配")
    return claims


def _base64_encode(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).decode("ascii").rstrip("=")


def _base64_decode(value: str) -> str:
    padding = "=" * (-len(value) % 4)
    return base64.urlsafe_b64decode(value + padding).decode("utf-8")
