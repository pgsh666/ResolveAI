import asyncio
import hashlib
import json
from typing import Any

import httpx
from opentelemetry import trace
from opentelemetry.propagate import inject
from opentelemetry.trace import SpanKind, Status, StatusCode

from app.core.config import Settings, get_settings
from app.core.observability import get_tracer, record_error, safe_payload, set_span_attributes
from app.tools.authorization import ApprovalTokenError, verify_approval_token
from app.tools.contracts import ToolKind, ToolPermission, validate_tool_call


class MCPToolCallError(RuntimeError):
    def __init__(self, tool_name: str, errors: list[str]) -> None:
        self.tool_name = tool_name
        self.errors = errors
        self.attempts = len(errors)
        super().__init__(
            f"MCP tool {tool_name} failed after {self.attempts} attempts: "
            f"{errors[-1] if errors else 'unknown error'}"
        )


class CommerceGateway:
    """Business tools behind an MCP boundary, with a local fallback for one-command demos."""

    def __init__(self, settings: Settings | None = None) -> None:
        self.settings = settings or get_settings()
        self.http_requests = 0

    def get_customer_context(self, order_id: str | None, customer_name: str) -> dict[str, Any]:
        return asyncio.run(self.aget_customer_context(order_id, customer_name))

    async def aget_customer_context(
        self, order_id: str | None, customer_name: str
    ) -> dict[str, Any]:
        if not order_id:
            return {
                "customer_name": customer_name, "customer_level": "unknown",
                "order_id": None, "order_status": "unknown", "order_amount": None,
                "paid": False, "context_available": False, "risk_flags": [],
                "tool_source": "not_called", "tool_attempts": 0,
                "tool_retry_count": 0, "tool_errors": [],
            }
        if self.settings.mcp_enabled and order_id:
            try:
                return await self.acall_tool(
                    "get_order_context",
                    {"order_id": order_id},
                    granted_permissions={ToolPermission.READ},
                )
            except Exception as error:
                context = self._local_context(order_id, customer_name)
                context["tool_fallback"] = f"MCP unavailable: {self._describe_exception(error)}"
                context["tool_attempts"] = getattr(error, "attempts", 1)
                context["tool_retry_count"] = max(0, context["tool_attempts"] - 1)
                context["tool_http_requests"] = self.http_requests
                context["tool_errors"] = getattr(
                    error,
                    "errors",
                    [self._describe_exception(error)],
                )
                return context
        return self._local_context(order_id, customer_name)

    async def acall_tool(
        self,
        tool_name: str,
        arguments: dict[str, Any],
        *,
        granted_permissions: set[ToolPermission] | set[str],
        approval_token: str | None = None,
        idempotency_key: str | None = None,
    ) -> dict[str, Any]:
        with get_tracer("tools").start_as_current_span(
            f"resolveai.tool.{tool_name}",
            kind=SpanKind.CLIENT,
            attributes={
                "resolveai.tool.name": tool_name,
                "resolveai.tool.arguments": safe_payload(arguments),
                "resolveai.tool.mcp_enabled": self.settings.mcp_enabled,
                "resolveai.tool.has_approval": bool(approval_token),
                "resolveai.tool.has_idempotency_key": bool(idempotency_key),
            },
            record_exception=False,
            set_status_on_exception=False,
        ) as span:
            try:
                result = await self._acall_tool_impl(
                    tool_name,
                    arguments,
                    granted_permissions=granted_permissions,
                    approval_token=approval_token,
                    idempotency_key=idempotency_key,
                )
                set_span_attributes(
                    span,
                    {
                        "resolveai.tool.result": safe_payload(result),
                        "resolveai.tool.source": result.get("tool_source"),
                        "resolveai.tool.attempts": result.get("tool_attempts", 1),
                        "resolveai.tool.retry_count": result.get("tool_retry_count", 0),
                    },
                )
                span.set_status(Status(StatusCode.OK))
                return result
            except BaseException as error:
                set_span_attributes(
                    span,
                    {
                        "resolveai.tool.attempts": getattr(error, "attempts", 1),
                        "resolveai.tool.retry_count": max(
                            0, int(getattr(error, "attempts", 1)) - 1
                        ),
                    },
                )
                record_error(span, error)
                raise

    async def _acall_tool_impl(
        self,
        tool_name: str,
        arguments: dict[str, Any],
        *,
        granted_permissions: set[ToolPermission] | set[str],
        approval_token: str | None = None,
        idempotency_key: str | None = None,
    ) -> dict[str, Any]:
        definition, validated = validate_tool_call(
            tool_name,
            arguments,
            granted_permissions,
        )
        payload = dict(validated)
        if definition.kind == ToolKind.WRITE:
            if not approval_token or not idempotency_key:
                raise ApprovalTokenError("写操作必须提供人工审批凭证和幂等键")
            payload.update(
                approval_token=approval_token,
                idempotency_key=idempotency_key,
            )

        if self.settings.mcp_enabled:
            errors: list[str] = []
            max_attempts = self.settings.tool_max_retries + 1
            for attempt in range(1, max_attempts + 1):
                try:
                    result = await self._call_mcp(tool_name, payload)
                    result.setdefault("tool_attempts", attempt)
                    result.setdefault("tool_retry_count", attempt - 1)
                    result.setdefault("tool_errors", errors)
                    result["tool_http_requests"] = self.http_requests
                    return result
                except Exception as error:
                    errors.append(self._describe_exception(error))
                    trace.get_current_span().add_event(
                        "resolveai.tool.attempt_failed",
                        {
                            "resolveai.tool.attempt": attempt,
                            "error.type": type(error).__name__,
                            "error.message": errors[-1],
                        },
                    )
                    if attempt < max_attempts and self.settings.retry_backoff_seconds:
                        await asyncio.sleep(
                            self.settings.retry_backoff_seconds * (2 ** (attempt - 1))
                        )
            raise MCPToolCallError(tool_name, errors)
        return self._local_tool(
            tool_name,
            validated,
            approval_token=approval_token,
            idempotency_key=idempotency_key,
        )

    async def _call_mcp(self, tool_name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        headers = {
            "Accept": "application/json, text/event-stream",
            "Content-Type": "application/json",
            "MCP-Protocol-Version": "2025-06-18",
        }
        inject(headers)
        initialize_request = {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "initialize",
            "params": {
                "protocolVersion": "2025-06-18",
                "capabilities": {},
                "clientInfo": {"name": "resolveai-backend", "version": "0.1.0"},
            },
        }
        tool_request = {
            "jsonrpc": "2.0",
            "id": 2,
            "method": "tools/call",
            "params": {"name": tool_name, "arguments": arguments},
        }

        # MCP is a loopback service. Ignoring system proxy settings prevents
        # localhost requests from being routed through an IDE/VPN HTTP proxy.
        async with httpx.AsyncClient(
            timeout=self.settings.tool_timeout_seconds,
            trust_env=False,
        ) as client:
            self.http_requests += 1
            initialize_response = await client.post(
                self.settings.mcp_server_url,
                headers=headers,
                json=initialize_request,
            )
            initialize_response.raise_for_status()
            initialize_payload = initialize_response.json()
            if initialize_payload.get("error"):
                raise RuntimeError(f"MCP initialize failed: {initialize_payload['error']}")

            self.http_requests += 1
            tool_response = await client.post(
                self.settings.mcp_server_url,
                headers=headers,
                json=tool_request,
            )
            tool_response.raise_for_status()
            payload = tool_response.json()

        if payload.get("error"):
            raise RuntimeError(f"MCP tool failed: {payload['error']}")
        result = payload.get("result", {})
        structured = result.get("structuredContent")
        if isinstance(structured, dict):
            return structured
        for content in result.get("content", []):
            text = content.get("text") if isinstance(content, dict) else None
            if text:
                return json.loads(text)
        raise RuntimeError(f"MCP tool {tool_name} returned no usable result")

    @staticmethod
    def _describe_exception(error: BaseException) -> str:
        messages: list[str] = []

        def collect(item: BaseException) -> None:
            nested = getattr(item, "exceptions", None)
            if nested:
                for child in nested:
                    collect(child)
                return
            message = str(item).strip()
            code = getattr(item, "code", None)
            data = getattr(item, "data", None)
            suffix = f" (code={code}, data={data})" if code is not None or data is not None else ""
            messages.append(
                f"{type(item).__name__}: {message}{suffix}"
                if message
                else f"{type(item).__name__}{suffix}"
            )

        collect(error)
        return " | ".join(messages)[:600] or type(error).__name__

    @staticmethod
    def _local_context(order_id: str | None, customer_name: str) -> dict[str, Any]:
        digits = "".join(char for char in (order_id or "1024") if char.isdigit())
        seed = int(digits[-3:] or 24)
        return {
            "customer_name": customer_name,
            "customer_level": "VIP" if seed % 3 == 0 else "standard",
            "order_id": order_id or f"ORD-{10000 + seed}",
            "order_status": "delayed" if seed % 2 else "delivered",
            "order_amount": float(79 + seed % 260),
            "paid": True,
            "risk_flags": [] if seed % 5 else ["recent_refund"],
            "tool_source": "mcp-local-adapter",
            "tool_attempts": 1,
            "tool_retry_count": 0,
            "tool_errors": [],
        }

    def _local_tool(
        self,
        tool_name: str,
        arguments: dict[str, Any],
        *,
        approval_token: str | None,
        idempotency_key: str | None,
    ) -> dict[str, Any]:
        if tool_name == "get_order_context":
            return self._local_context(arguments["order_id"], "")

        if tool_name == "calculate_refund_quote":
            context = self._local_context(arguments["order_id"], "")
            approved_amount = min(arguments["requested_amount"], context["order_amount"])
            return {
                "order_id": arguments["order_id"],
                "approved_amount": approved_amount,
                "requires_approval": approved_amount > 100 or bool(context["risk_flags"]),
                "reason": (
                    "金额超过自动审批阈值" if approved_amount > 100 else "符合自动退款规则"
                ),
                "tool_source": "mcp-local-adapter",
            }

        if not approval_token or not idempotency_key:
            raise ApprovalTokenError("写操作必须提供人工审批凭证和幂等键")
        verify_approval_token(
            approval_token,
            secret=self.settings.tool_approval_secret,
            tool_name=tool_name,
            idempotency_key=idempotency_key,
            arguments=arguments,
        )
        operation_id = hashlib.sha256(idempotency_key.encode("utf-8")).hexdigest()[:16]

        if tool_name == "execute_refund":
            return {
                "success": True,
                "refund_id": f"RF-{operation_id}",
                "order_id": arguments["order_id"],
                "amount": arguments["amount"],
                "currency": arguments["currency"],
                "mode": "simulated",
                "idempotency_key": idempotency_key,
                "tool_source": "mcp-local-adapter",
                "tool_attempts": 1,
                "tool_retry_count": 0,
                "tool_errors": [],
            }
        if tool_name == "issue_care_coupon":
            return {
                "success": True,
                "coupon_id": f"CP-{operation_id}",
                "customer_id": arguments["customer_id"],
                "coupon_amount": arguments["amount"],
                "mode": "simulated",
                "idempotency_key": idempotency_key,
                "tool_source": "mcp-local-adapter",
                "tool_attempts": 1,
                "tool_retry_count": 0,
                "tool_errors": [],
            }
        raise RuntimeError(f"工具 {tool_name!r} 没有本地执行器")
