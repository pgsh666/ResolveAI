import re
from typing import Any

from pydantic import BaseModel, Field


class SecurityAssessment(BaseModel):
    prompt_injection: bool = False
    unauthorized_request: bool = False
    sensitive_operation: bool = False
    pii_types: list[str] = Field(default_factory=list)
    risk_level: str = "low"
    requires_human_review: bool = False
    blocked: bool = False
    reasons: list[str] = Field(default_factory=list)


_PROMPT_INJECTION_PATTERNS = [
    re.compile(pattern, re.IGNORECASE)
    for pattern in (
        r"忽略.{0,12}(系统|之前|以上|原有).{0,12}(指令|提示|规则|政策)",
        r"(覆盖|替换|无视).{0,12}(系统|开发者|安全).{0,12}(指令|规则|策略)",
        r"(泄露|显示|输出|告诉我).{0,12}(系统提示|system prompt|隐藏指令)",
        r"\b(ignore|disregard|override)\b.{0,40}\b(system|developer|instruction|policy)\b",
        r"\b(jailbreak|developer mode|dan mode)\b",
    )
]

_UNAUTHORIZED_PATTERNS = [
    re.compile(pattern, re.IGNORECASE)
    for pattern in (
        r"(不用|无需|不要|跳过|绕过|免除).{0,12}(审批|核验|核对|验证|检查|权限|风控)",
        r"不要.{0,12}(审批|核验|核对|验证|检查).{0,20}(直接|立刻|马上)",
        r"(直接|立刻|马上).{0,20}(退款|返还|补发|修改|关闭).{0,12}(不用|无需|跳过|绕过)",
        r"我是.{0,8}(内部人员|管理员|主管).{0,20}(直接|无需|不用)",
        r"(查询|显示|告诉).{0,10}(密码|验证码|支付密码|完整银行卡号)",
    )
]

_SENSITIVE_OPERATION_PATTERNS = [
    ("ACCOUNT_DISABLE_2FA", re.compile(r"(关闭|取消|绕过).{0,8}(二次验证|双重验证|2fa)", re.IGNORECASE)),
    ("ACCOUNT_CHANGE_BINDING", re.compile(r"(修改|更换|解绑).{0,8}(绑定手机|绑定邮箱|手机号)", re.IGNORECASE)),
    ("ACCOUNT_TAKEOVER", re.compile(r"(疑似.{0,6}(盗号|被盗)|陌生.{0,6}(设备|登录|交易)|敏感交易|升级安全团队)", re.IGNORECASE)),
    ("CREDENTIAL_REQUEST", re.compile(r"(告诉|查询|提供|获取).{0,10}(密码|验证码|支付密码|完整银行卡号)", re.IGNORECASE)),
    ("HIGH_VALUE_INCIDENT", re.compile(r"高价值.{0,20}(丢失|丢件|故障|损坏|赔付|商品)", re.IGNORECASE)),
    ("BULK_PRODUCT_FAILURE", re.compile(r"(批量故障|多件.{0,10}(故障|损坏))", re.IGNORECASE)),
]

_MOBILE_RE = re.compile(r"(?<!\d)(1[3-9]\d)\d{4}(\d{4})(?!\d)")
_ID_CARD_RE = re.compile(r"(?<![0-9A-Za-z])(\d{3})\d{11}(\d{3}[0-9Xx])(?![0-9A-Za-z])")
_BANK_CARD_RE = re.compile(r"(?<!\d)(\d{4})\d{8,11}(\d{4})(?!\d)")
_EMAIL_RE = re.compile(r"(?<![\w.+-])([\w.+-])([\w.+-]*)@([\w.-]+\.[A-Za-z]{2,})(?![\w.-])")
_SECRET_RE = re.compile(
    r"((?:短信)?验证码|支付密码|登录密码|密码)\s*[:：=]\s*([A-Za-z0-9!@#$%^&*._-]{4,})",
    re.IGNORECASE,
)


def redact_sensitive_text(value: str | None) -> tuple[str, list[str]]:
    if not value:
        return value or "", []
    detected: list[str] = []
    text = value

    def replace_mobile(match: re.Match) -> str:
        detected.append("mobile_phone")
        return f"{match.group(1)}****{match.group(2)}"

    def replace_id_card(match: re.Match) -> str:
        detected.append("identity_card")
        return f"{match.group(1)}***********{match.group(2)}"

    def replace_bank_card(match: re.Match) -> str:
        detected.append("bank_card")
        return f"{match.group(1)}********{match.group(2)}"

    def replace_email(match: re.Match) -> str:
        detected.append("email")
        return f"{match.group(1)}***@{match.group(3)}"

    def replace_secret(match: re.Match) -> str:
        detected.append("credential")
        return f"{match.group(1)}：[已脱敏]"

    text = _MOBILE_RE.sub(replace_mobile, text)
    text = _ID_CARD_RE.sub(replace_id_card, text)
    text = _BANK_CARD_RE.sub(replace_bank_card, text)
    text = _EMAIL_RE.sub(replace_email, text)
    text = _SECRET_RE.sub(replace_secret, text)
    return text, sorted(set(detected))


def redact_text(value: str | None) -> str:
    return redact_sensitive_text(value)[0]


def redact_conversation(messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [
        {**item, "content": redact_text(str(item.get("content", "")))}
        for item in messages
    ]


class SecurityService:
    def assess(
        self,
        *,
        subject: str,
        message: str,
        conversation: list[dict[str, Any]] | None = None,
    ) -> SecurityAssessment:
        conversation_text = " ".join(
            str(item.get("content", "")) for item in (conversation or [])[-12:]
        )
        text = " ".join((subject, message, conversation_text)).strip()
        _, pii_types = redact_sensitive_text(text)
        injection = any(pattern.search(text) for pattern in _PROMPT_INJECTION_PATTERNS)
        unauthorized = any(pattern.search(text) for pattern in _UNAUTHORIZED_PATTERNS)
        sensitive_reasons = [
            code for code, pattern in _SENSITIVE_OPERATION_PATTERNS if pattern.search(text)
        ]
        reasons: list[str] = []
        if injection:
            reasons.append("PROMPT_INJECTION_DETECTED")
        if unauthorized:
            reasons.append("UNAUTHORIZED_BYPASS_REQUEST")
        reasons.extend(sensitive_reasons)
        requires_review = bool(reasons)
        return SecurityAssessment(
            prompt_injection=injection,
            unauthorized_request=unauthorized,
            sensitive_operation=bool(sensitive_reasons),
            pii_types=pii_types,
            risk_level="high" if requires_review else ("medium" if pii_types else "low"),
            requires_human_review=requires_review,
            blocked=requires_review,
            reasons=list(dict.fromkeys(reasons)),
        )


__all__ = [
    "SecurityAssessment",
    "SecurityService",
    "redact_conversation",
    "redact_sensitive_text",
    "redact_text",
]
