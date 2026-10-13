from typing import Any

from opentelemetry.trace import SpanKind, Status, StatusCode
from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.config import Settings, get_settings
from app.core.observability import get_tracer, record_error, safe_payload, set_span_attributes
from app.models import Approval, AuditEvent, Ticket, ToolExecution, utc_now
from app.services.commerce import CommerceGateway
from app.services.auth import current_actor
from app.tools.authorization import canonical_argument_hash, create_approval_token
from app.tools.contracts import (
    ToolKind,
    ToolPermission,
    ToolPermissionError,
    UnknownToolError,
    validate_tool_call,
)


class ToolExecutionError(RuntimeError):
    pass


class ApprovalRequiredError(ToolExecutionError):
    pass


class IdempotencyConflictError(ToolExecutionError):
    pass


class ToolBusinessRuleError(ToolExecutionError):
    pass


class ToolExecutionService:
    def __init__(
        self,
        session: Session,
        *,
        settings: Settings | None = None,
        gateway: CommerceGateway | None = None,
    ) -> None:
        self.session = session
        self.settings = settings or get_settings()
        self.gateway = gateway or CommerceGateway(self.settings)

    async def execute_write(
        self,
        *,
        ticket: Ticket,
        tool_name: str,
        arguments: dict[str, Any],
        idempotency_key: str,
    ) -> tuple[ToolExecution, bool]:
        with get_tracer("tools").start_as_current_span(
            "resolveai.tool.execute_write",
            kind=SpanKind.INTERNAL,
            attributes={
                "resolveai.ticket.id": ticket.id,
                "resolveai.ticket.no": ticket.ticket_no,
                "resolveai.tool.name": tool_name,
                "resolveai.tool.arguments": safe_payload(arguments),
                "resolveai.tool.idempotency_key_hash": canonical_argument_hash(
                    {"idempotency_key": idempotency_key}
                )[:16],
            },
            record_exception=False,
            set_status_on_exception=False,
        ) as span:
            try:
                execution, replayed = await self._execute_write_impl(
                    ticket=ticket,
                    tool_name=tool_name,
                    arguments=arguments,
                    idempotency_key=idempotency_key,
                )
                set_span_attributes(
                    span,
                    {
                        "resolveai.tool.execution_id": execution.id,
                        "resolveai.tool.status": execution.status,
                        "resolveai.tool.replayed": replayed,
                        "resolveai.tool.result": safe_payload(execution.result),
                        "resolveai.tool.retry_count": (execution.result or {}).get(
                            "tool_retry_count", 0
                        ),
                    },
                )
                span.set_status(Status(StatusCode.OK))
                return execution, replayed
            except BaseException as error:
                record_error(span, error)
                raise

    async def _execute_write_impl(
        self,
        *,
        ticket: Ticket,
        tool_name: str,
        arguments: dict[str, Any],
        idempotency_key: str,
    ) -> tuple[ToolExecution, bool]:
        definition, validated = validate_tool_call(
            tool_name,
            arguments,
            {ToolPermission.WRITE},
        )
        if definition.kind != ToolKind.WRITE:
            raise ToolPermissionError(f"查询工具 {tool_name!r} 不能通过写操作接口执行")

        request_hash = canonical_argument_hash(
            {"ticket_id": ticket.id, "tool_name": tool_name, "arguments": validated}
        )
        existing = self.session.scalar(
            select(ToolExecution).where(ToolExecution.idempotency_key == idempotency_key)
        )
        if existing:
            return self._resolve_duplicate(existing, request_hash)

        approval = self.session.scalar(
            select(Approval).where(Approval.ticket_id == ticket.id)
        )
        if not approval or approval.state != "approved" or not approval.reviewed_by:
            raise ApprovalRequiredError("写操作必须先由人工审批通过")

        self._validate_business_scope(ticket, approval, tool_name, validated)
        approval_execution = self.session.scalar(
            select(ToolExecution).where(
                ToolExecution.approval_id == approval.id,
                ToolExecution.tool_name == tool_name,
            )
        )
        if approval_execution:
            if approval_execution.request_hash == request_hash:
                return self._resolve_duplicate(approval_execution, request_hash)
            raise IdempotencyConflictError("该人工审批已经用于执行同类写操作")

        execution = ToolExecution(
            ticket_id=ticket.id,
            approval_id=approval.id,
            tool_name=tool_name,
            tool_kind=definition.kind.value,
            idempotency_key=idempotency_key,
            request_hash=request_hash,
            arguments=validated,
            status="executing",
            executed_by=current_actor().username if self.settings.auth_enabled else approval.reviewed_by,
        )
        self.session.add(execution)
        try:
            self.session.commit()
        except IntegrityError:
            self.session.rollback()
            existing = self.session.scalar(
                select(ToolExecution).where(ToolExecution.idempotency_key == idempotency_key)
            )
            if existing is None:
                existing = self.session.scalar(
                    select(ToolExecution).where(
                        ToolExecution.approval_id == approval.id,
                        ToolExecution.tool_name == tool_name,
                    )
                )
            if existing:
                return self._resolve_duplicate(existing, request_hash)
            raise
        self.session.refresh(execution)

        approval_token = create_approval_token(
            secret=self.settings.tool_approval_secret,
            ticket_id=ticket.id,
            approval_id=approval.id,
            tool_name=tool_name,
            idempotency_key=idempotency_key,
            arguments=validated,
        )
        try:
            result = await self.gateway.acall_tool(
                tool_name,
                validated,
                granted_permissions={ToolPermission.WRITE},
                approval_token=approval_token,
                idempotency_key=idempotency_key,
            )
        except Exception as error:
            execution.status = "failed"
            execution.error = f"{type(error).__name__}: {error}"[:1000]
            attempts = int(getattr(error, "attempts", 1))
            execution.result = {
                "success": False,
                "tool_attempts": attempts,
                "tool_retry_count": max(0, attempts - 1),
                "tool_errors": list(getattr(error, "errors", [str(error)])),
            }
            execution.completed_at = utc_now()
            self.session.commit()
            raise ToolExecutionError(f"工具执行失败：{error}") from error

        execution.result = result
        execution.status = "completed"
        execution.completed_at = utc_now()
        self.session.add(
            AuditEvent(
                ticket_id=ticket.id,
                agent="工具执行器",
                action=f"执行写工具：{tool_name}",
                summary=f"人工审批后已执行 {tool_name}",
                details={
                    "tool_name": tool_name,
                    "approval_id": approval.id,
                    "idempotency_key": idempotency_key,
                    "arguments": validated,
                    "result": result,
                },
            )
        )
        self.session.commit()
        self.session.refresh(execution)
        return execution, False

    @staticmethod
    def _resolve_duplicate(
        existing: ToolExecution,
        request_hash: str,
    ) -> tuple[ToolExecution, bool]:
        if existing.request_hash != request_hash:
            raise IdempotencyConflictError("该幂等键已被其他工具调用或不同参数占用")
        if existing.status != "completed":
            raise IdempotencyConflictError(
                f"该幂等请求当前状态为 {existing.status!r}，不能重复执行"
            )
        return existing, True

    @staticmethod
    def _validate_business_scope(
        ticket: Ticket,
        approval: Approval,
        tool_name: str,
        arguments: dict[str, Any],
    ) -> None:
        if tool_name != "execute_refund":
            if "关怀券" not in approval.action:
                raise ToolBusinessRuleError("该审批没有授权发放关怀券")
            if arguments.get("customer_id") not in {ticket.id, ticket.customer_name}:
                raise ToolBusinessRuleError("关怀券客户必须与审批工单中的客户一致")
            return
        if "退款" not in approval.action:
            raise ToolBusinessRuleError("该审批没有授权执行退款")
        if not ticket.order_id:
            raise ToolBusinessRuleError("退款工单缺少已核验的订单号")
        if arguments["order_id"] != ticket.order_id:
            raise ToolBusinessRuleError("退款订单号必须与审批工单中的订单号一致")
        if ticket.category and ticket.category not in {"refund", "payment"}:
            raise ToolBusinessRuleError("该工单分类不允许执行退款")
        if approval.amount is None:
            raise ToolBusinessRuleError("审批记录缺少允许退款金额")
        if arguments["amount"] > approval.amount:
            raise ToolBusinessRuleError("退款金额不能超过人工审批金额")
        context = (ticket.assessment or {}).get("customer_context", {})
        if context.get("context_available") is False or context.get("tool_fallback") or context.get("paid") is False:
            raise ToolBusinessRuleError("退款必须依据有效的已付款订单上下文，不能使用故障降级结果")
        paid_amount = context.get("order_amount")
        if paid_amount is not None and arguments["amount"] > paid_amount:
            raise ToolBusinessRuleError("退款金额不能超过已核验的订单实付金额")


__all__ = [
    "ApprovalRequiredError",
    "IdempotencyConflictError",
    "ToolBusinessRuleError",
    "ToolExecutionError",
    "ToolExecutionService",
    "UnknownToolError",
    "ValidationError",
]
