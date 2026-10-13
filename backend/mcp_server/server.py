"""Standalone MCP server exposing auditable commerce tools to the agent backend."""

import hashlib
from typing import Any

from mcp.server import MCPServer

from app.core.config import get_settings
from app.tools.authorization import verify_approval_token
from app.tools.contracts import ToolPermission, validate_tool_call


mcp = MCPServer("ResolveAI Commerce Tools")
settings = get_settings()


def _order_context(order_id: str) -> dict[str, Any]:
    digits = "".join(char for char in order_id if char.isdigit())
    seed = int(digits[-3:] or 24)
    return {
        "customer_level": "VIP" if seed % 3 == 0 else "standard",
        "order_id": order_id,
        "order_status": "delayed" if seed % 2 else "delivered",
        "order_amount": float(79 + seed % 260),
        "paid": True,
        "risk_flags": [] if seed % 5 else ["recent_refund"],
        "tool_source": "mcp-server",
    }


@mcp.tool()
def get_order_context(order_id: str) -> dict[str, Any]:
    """Return order, payment, customer tier, and risk context for a support ticket."""
    _, arguments = validate_tool_call(
        "get_order_context",
        {"order_id": order_id},
        {ToolPermission.READ},
    )
    return _order_context(arguments["order_id"])


@mcp.tool()
def calculate_refund_quote(order_id: str, requested_amount: float) -> dict[str, Any]:
    """Validate a requested refund amount and report whether human approval is required."""
    _, arguments = validate_tool_call(
        "calculate_refund_quote",
        {"order_id": order_id, "requested_amount": requested_amount},
        {ToolPermission.READ},
    )
    context = _order_context(arguments["order_id"])
    approved_amount = min(arguments["requested_amount"], context["order_amount"])
    return {
        "order_id": arguments["order_id"],
        "approved_amount": approved_amount,
        "requires_approval": approved_amount > 100 or bool(context["risk_flags"]),
        "reason": "金额超过自动审批阈值" if approved_amount > 100 else "符合自动退款规则",
        "tool_source": "mcp-server",
    }


@mcp.tool()
def execute_refund(
    order_id: str,
    amount: float,
    reason: str,
    currency: str,
    idempotency_key: str,
    approval_token: str,
) -> dict[str, Any]:
    """Execute a simulated refund only with a signed human-approval credential."""
    _, arguments = validate_tool_call(
        "execute_refund",
        {
            "order_id": order_id,
            "amount": amount,
            "reason": reason,
            "currency": currency,
        },
        {ToolPermission.WRITE},
    )
    verify_approval_token(
        approval_token,
        secret=settings.tool_approval_secret,
        tool_name="execute_refund",
        idempotency_key=idempotency_key,
        arguments=arguments,
    )
    operation_id = hashlib.sha256(idempotency_key.encode("utf-8")).hexdigest()[:16]
    return {
        "success": True,
        "refund_id": f"RF-{operation_id}",
        "order_id": arguments["order_id"],
        "amount": arguments["amount"],
        "currency": arguments["currency"],
        "mode": "simulated",
        "idempotency_key": idempotency_key,
        "tool_source": "mcp-server",
    }


@mcp.tool()
def issue_care_coupon(
    customer_id: str,
    amount: float,
    idempotency_key: str,
    approval_token: str,
) -> dict[str, Any]:
    """Issue a simulated coupon only with a signed human-approval credential."""
    _, arguments = validate_tool_call(
        "issue_care_coupon",
        {"customer_id": customer_id, "amount": amount},
        {ToolPermission.WRITE},
    )
    verify_approval_token(
        approval_token,
        secret=settings.tool_approval_secret,
        tool_name="issue_care_coupon",
        idempotency_key=idempotency_key,
        arguments=arguments,
    )
    operation_id = hashlib.sha256(idempotency_key.encode("utf-8")).hexdigest()[:16]
    return {
        "success": True,
        "coupon_id": f"CP-{operation_id}",
        "customer_id": arguments["customer_id"],
        "coupon_amount": arguments["amount"],
        "mode": "simulated",
        "idempotency_key": idempotency_key,
        "tool_source": "mcp-server",
    }


if __name__ == "__main__":
    mcp.run(
        transport="streamable-http",
        host=settings.mcp_server_host,
        port=settings.mcp_server_port,
        json_response=True,
        stateless_http=True,
    )
