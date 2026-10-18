import asyncio
import json
import re
from typing import Literal
from datetime import timedelta

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from pydantic import ValidationError
from sqlalchemy import func, select, text, or_, update
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session
from starlette.concurrency import run_in_threadpool
from starlette.responses import StreamingResponse

from app.agents.graph import graph_topology
from app.core.config import Settings, get_settings
from app.core.database import get_db
from app.models import (
    Approval,
    AuditEvent,
    KnowledgeDocument,
    Ticket,
    TicketAccess,
    ToolExecution,
    WorkflowCheckpoint,
    QuestionRoute,
    KnowledgeAccess,
    new_id,
    utc_now,
)
from app.schemas import (
    AgentRunOut,
    BackgroundTaskOut,
    BatchEvaluationRequest,
    CustomerConfirmationRequest,
    CustomerMessageRequest,
    CustomerTicketCreate,
    CustomerTicketOut,
    DashboardResponse,
    DecisionRequest,
    DraftRewriteRequest,
    DraftSendRequest,
    KnowledgeDocumentOut,
    KnowledgeImportRequest,
    KnowledgeImportItem,
    KnowledgeToggle,
    HealthResponse,
    TicketCreate,
    TicketDetail,
    TicketSummary,
    ToolExecutionOut,
    ToolExecutionRequest,
    WorkflowCheckpointOut,
)
from app.services.retrieval import RetrievalService
from app.services.embeddings import EmbeddingService
from app.services.analytics import build_activity_trend
from app.services.llm import LLMService
from app.services.performance import build_performance_report
from app.services.auth import (assert_ticket_access, assert_task_access, attach_ticket_access, attach_task_access,
                               authorize_request, current_actor, ticket_scope)
from app.services.auth import knowledge_scope
from app.services.numbering import next_number
from app.services.question_routing import begin_question, answer_question
from app.services.reply_validation import check_draft_facts
from app.services.security import redact_conversation, redact_text
from app.services.workflow import WorkflowService, ticket_conversation, ticket_to_detail
from app.services.tool_execution import (
    ApprovalRequiredError,
    IdempotencyConflictError,
    ToolBusinessRuleError,
    ToolExecutionError,
    ToolExecutionService,
)
from app.tools.contracts import ToolPermissionError, UnknownToolError, tool_catalog
from app.tasks.service import (
    TERMINAL_TASK_STATUSES,
    BackgroundTaskNotFoundError,
    TaskQueueService,
    TaskQueueUnavailableError,
)


router = APIRouter(dependencies=[Depends(authorize_request)])


def get_task_queue_service(settings: Settings = Depends(get_settings)) -> TaskQueueService:
    service = TaskQueueService(settings)
    try:
        service.ensure_available()
    except TaskQueueUnavailableError as error:
        raise HTTPException(status_code=503, detail=str(error)) from error
    return service


def _task_response(snapshot: dict, settings: Settings) -> dict:
    return {
        **snapshot,
        "event_url": f"{settings.api_prefix}/tasks/{snapshot['id']}/events",
    }


def _get_task_snapshot(service: TaskQueueService, task_id: str) -> dict:
    try:
        return service.snapshot(task_id)
    except BackgroundTaskNotFoundError as error:
        raise HTTPException(status_code=404, detail="后台任务不存在或状态已过期") from error
    except TaskQueueUnavailableError as error:
        raise HTTPException(status_code=503, detail=str(error)) from error


def _encode_sse(event: str, snapshot: dict) -> str:
    payload = BackgroundTaskOut.model_validate(snapshot).model_dump(mode="json")
    return f"event: {event}\ndata: {json.dumps(payload, ensure_ascii=False)}\n\n"


def get_ticket_or_404(ticket_id: str, session: Session) -> Ticket:
    ticket = session.scalar(select(Ticket).where(or_(Ticket.id == ticket_id, Ticket.ticket_no == ticket_id,
                                                    Ticket.legacy_ticket_no == ticket_id), ticket_scope()))
    if not ticket:
        raise HTTPException(status_code=404, detail="工单不存在")
    assert_ticket_access(ticket.id, session)
    return ticket


def get_customer_ticket_or_404(ticket_id: str, session: Session) -> Ticket:
    assert_ticket_access(ticket_id, session, customer_action=True)
    ticket = get_ticket_or_404(ticket_id, session)
    access = session.get(TicketAccess, ticket.id)
    if access and access.customer_deleted_at is not None:
        raise HTTPException(status_code=404, detail="对话不存在")
    return ticket


def reply_facts(ticket, session):
    assessment = ticket.assessment or {}
    facts = {"order_id": ticket.order_id, "subject": ticket.subject, "message": ticket.message,
             "amount": ticket.amount, "completed_operations": [
                 {"tool_name": item.tool_name, "result": item.result}
                 for item in session.scalars(select(ToolExecution).where(
                     ToolExecution.ticket_id == ticket.id, ToolExecution.status == "completed")).all()]}
    return facts, assessment.get("customer_context") or {"context_available": False}, assessment.get("policy") or {}, assessment.get("citations") or []


def validate_reply_before_send(ticket, session, response):
    facts, context, policy, citations = reply_facts(ticket, session)
    checks = check_draft_facts(facts, context, policy, {"draft_response": response, "refund_amount": None}, citations)
    failed = [name for name, valid in checks.items() if not valid]
    if failed:
        raise HTTPException(422, "回复未通过事实校验：" + "、".join(failed))
    return checks


def ticket_to_customer_view(ticket: Ticket) -> CustomerTicketOut:
    assessment = ticket.assessment or {}
    confirmation = assessment.get("customer_confirmation") or {}
    sent_response = redact_text(assessment.get("sent_response"))
    messages = redact_conversation(ticket_conversation(ticket))
    last_message = messages[-1] if messages else None

    if confirmation:
        stage = "resolved" if confirmation.get("resolved") else "follow_up"
    elif last_message and last_message.get("role") == "support":
        stage = "answered"
    elif ticket.status in {"pending_approval", "needs_review"}:
        stage = "reviewing"
    elif len(messages) > 1 or ticket.status == "in_progress" or assessment.get("confidence_breakdown"):
        stage = "processing"
    else:
        stage = "submitted"

    return CustomerTicketOut(
        id=ticket.id,
        ticket_no=ticket.ticket_no,
        customer_name=redact_text(ticket.customer_name),
        subject=redact_text(ticket.subject),
        message=redact_text(ticket.message),
        stage=stage,
        sent_response=sent_response,
        sent_at=assessment.get("sent_at"),
        customer_confirmed=confirmation.get("resolved") if confirmation else None,
        customer_rating=confirmation.get("rating"),
        customer_feedback=confirmation.get("feedback"),
        messages=messages,
        handed_off=assessment.get("handoff", {}).get("active", False),
        handoff_reasons=assessment.get("handoff", {}).get("reasons", []),
        created_at=ticket.created_at,
        updated_at=ticket.updated_at,
    )


@router.get("/health", response_model=HealthResponse, tags=["system"])
def health(
    settings: Settings = Depends(get_settings),
    session: Session = Depends(get_db),
) -> HealthResponse:
    try:
        session.execute(text("SELECT 1")).scalar_one()
        knowledge_documents = session.scalar(select(func.count(KnowledgeDocument.id))) or 0
    except SQLAlchemyError as error:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="数据库健康检查失败",
        ) from error
    queue_check = "disabled"
    if settings.async_tasks_enabled:
        try:
            queue_health = TaskQueueService(settings).health()
            queue_check = (
                f"ready:{queue_health['workers']}-worker"
                if queue_health["workers"]
                else "redis-ok:no-worker"
            )
        except TaskQueueUnavailableError:
            queue_check = "unavailable"
    return HealthResponse(
        status="ok",
        app=settings.app_name,
        agent_mode=settings.agent_mode,
        model=settings.active_model,
        database="PostgreSQL + pgvector" if settings.is_postgres else "SQLite demo",
        database_ready=True,
        knowledge_documents=knowledge_documents,
        mcp_enabled=settings.mcp_enabled,
        checks={
            "database": "ok",
            "mcp": "enabled" if settings.mcp_enabled else "disabled",
            "tracing": "enabled" if settings.otel_enabled else "disabled",
            "queue": queue_check,
            "embedding": EmbeddingService(settings).profile,
        },
    )


@router.get("/observability", tags=["system"])
def observability(settings: Settings = Depends(get_settings)) -> dict:
    """Expose non-secret tracing configuration for deployment verification."""
    return {
        "enabled": settings.otel_enabled,
        "provider": "opentelemetry",
        "exporter": (
            "otlp-http"
            if settings.otel_exporter_otlp_traces_endpoint
            else ("console" if settings.otel_console_exporter else "none")
        ),
        "service_name": settings.otel_service_name,
        "sample_ratio": settings.otel_sample_ratio,
        "prompt_version": settings.prompt_version,
        "ui_url": settings.otel_ui_url,
        "privacy": {
            "raw_prompts_exported": False,
            "raw_customer_messages_exported": False,
            "secrets_exported": False,
        },
    }


@router.get("/tools", tags=["tools"])
def list_tools() -> dict:
    """Expose the allowlisted tool catalog and each tool's JSON Schema."""
    return {"tools": tool_catalog()}


@router.post(
    "/tickets/{ticket_id}/tools/{tool_name}/execute",
    response_model=ToolExecutionOut,
    tags=["tools"],
)
async def execute_ticket_write_tool(
    ticket_id: str,
    tool_name: str,
    payload: ToolExecutionRequest,
    session: Session = Depends(get_db),
) -> ToolExecutionOut:
    ticket = get_ticket_or_404(ticket_id, session)
    try:
        execution, replayed = await ToolExecutionService(session).execute_write(
            ticket=ticket,
            tool_name=tool_name,
            arguments=payload.arguments,
            idempotency_key=payload.idempotency_key,
        )
    except UnknownToolError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error
    except ValidationError as error:
        raise HTTPException(
            status_code=422,
            detail={"message": "工具参数校验失败", "errors": error.errors(include_url=False)},
        ) from error
    except (ApprovalRequiredError, ToolPermissionError) as error:
        raise HTTPException(status_code=403, detail=str(error)) from error
    except (IdempotencyConflictError, ToolBusinessRuleError) as error:
        raise HTTPException(status_code=409, detail=str(error)) from error
    except ToolExecutionError as error:
        raise HTTPException(status_code=502, detail=str(error)) from error

    return ToolExecutionOut.model_validate(execution).model_copy(
        update={"replayed": replayed}
    )


@router.get(
    "/tickets/{ticket_id}/tool-executions",
    response_model=list[ToolExecutionOut],
    tags=["tools"],
)
def list_ticket_tool_executions(
    ticket_id: str,
    session: Session = Depends(get_db),
) -> list[ToolExecution]:
    get_ticket_or_404(ticket_id, session)
    statement = (
        select(ToolExecution)
        .where(ToolExecution.ticket_id == ticket_id)
        .order_by(ToolExecution.created_at.asc())
    )
    return list(session.scalars(statement).all())


@router.get(
    "/tickets/{ticket_id}/checkpoint",
    response_model=WorkflowCheckpointOut,
    tags=["agents"],
)
def get_ticket_checkpoint(
    ticket_id: str,
    session: Session = Depends(get_db),
) -> WorkflowCheckpoint:
    get_ticket_or_404(ticket_id, session)
    checkpoint = session.scalar(
        select(WorkflowCheckpoint).where(WorkflowCheckpoint.ticket_id == ticket_id)
    )
    if checkpoint is None:
        raise HTTPException(status_code=404, detail="该工单还没有 Agent Checkpoint")
    return checkpoint


@router.post(
    "/tickets/{ticket_id}/resume",
    response_model=TicketDetail,
    tags=["agents"],
)
async def resume_ticket_workflow(
    ticket_id: str,
    session: Session = Depends(get_db),
) -> TicketDetail:
    ticket = get_ticket_or_404(ticket_id, session)
    checkpoint = session.scalar(
        select(WorkflowCheckpoint).where(WorkflowCheckpoint.ticket_id == ticket_id)
    )
    if checkpoint is None:
        raise HTTPException(status_code=404, detail="该工单还没有可恢复的 Checkpoint")
    if checkpoint.status == "waiting_approval":
        raise HTTPException(status_code=409, detail="工作流正在等待人工审批，请先处理审批")
    if checkpoint.status == "completed":
        return ticket_to_detail(session, ticket)
    try:
        return await WorkflowService(session).analyze(ticket)
    except Exception as error:
        session.rollback()
        raise HTTPException(status_code=500, detail=f"Checkpoint 恢复失败：{error}") from error


@router.get("/dashboard", response_model=DashboardResponse, tags=["dashboard"])
def dashboard(session: Session = Depends(get_db)) -> DashboardResponse:
    visible_tickets = select(Ticket.id).where(ticket_scope())
    total = session.scalar(select(func.count(Ticket.id)).where(ticket_scope())) or 0
    resolved = (
        session.scalar(select(func.count(Ticket.id)).where(Ticket.status == "resolved", ticket_scope())) or 0
    )
    pending = (
        session.scalar(select(func.count(Approval.id)).where(Approval.state == "pending", Approval.ticket_id.in_(visible_tickets))) or 0
    )
    resolution_rate = int((resolved / total) * 100) if total else 0
    average_duration_ms = float(
        session.scalar(select(func.avg(AuditEvent.duration_ms)).where(AuditEvent.ticket_id.in_(visible_tickets))) or 0
    )
    duration_label = (
        f"{average_duration_ms / 1000:.1f} 秒"
        if average_duration_ms >= 1000
        else f"{round(average_duration_ms)} 毫秒"
    )
    event_rows = session.execute(
        select(AuditEvent.created_at, AuditEvent.agent, AuditEvent.ticket_id).where(
            AuditEvent.created_at >= utc_now() - timedelta(days=8)
            , AuditEvent.ticket_id.in_(visible_tickets)
        )
    ).all()
    trend = build_activity_trend(event_rows)

    category_labels = {
        "refund": "退款售后",
        "logistics": "物流问题",
        "payment": "支付问题",
        "account": "账号问题",
        "product": "商品问题",
        "other": "其他",
        None: "待分析",
    }
    category_colors = {
        "refund": "#7057ff",
        "logistics": "#22b8a7",
        "payment": "#f59e61",
        "account": "#58a5c2",
        "product": "#df6f8f",
        "other": "#a8b2c1",
        None: "#c7ccd5",
    }
    category_rows = session.execute(
        select(Ticket.category, func.count(Ticket.id)).where(ticket_scope()).group_by(Ticket.category)
    ).all()
    categories = [
        {
            "name": category_labels.get(category, category or "待分析"),
            "value": round(count / total * 100) if total else 0,
            "color": category_colors.get(category, "#a8b2c1"),
        }
        for category, count in category_rows
    ]
    return DashboardResponse(
        metrics=[
            {"label": "对话总量", "value": str(total), "change": "数据库实时统计", "tone": "neutral"},
            {"label": "当前解决率", "value": f"{resolution_rate}%", "change": f"{resolved}/{total} 张已解决", "tone": "positive" if resolved else "neutral"},
            {"label": "Agent 平均耗时", "value": duration_label, "change": "基于审计记录", "tone": "neutral"},
            {"label": "待人工审批", "value": str(pending), "change": "高风险动作", "tone": "warning" if pending else "neutral"},
        ],
        trend=trend,
        categories=categories,
        pending_approvals=pending,
        agent_success_rate=float(resolution_rate),
    )


@router.get("/tickets", response_model=list[TicketSummary], tags=["tickets"])
def list_tickets(
    ticket_status: str | None = Query(default=None, alias="status"),
    q: str = Query(default="", max_length=100),
    case_type: Literal["all", "consultation", "after_sales"] = "all",
    session: Session = Depends(get_db),
) -> list[Ticket]:
    statement = select(Ticket).where(ticket_scope()).order_by(Ticket.created_at.desc())
    if ticket_status == "processing":
        statement = statement.where(
            Ticket.status.in_({"in_progress", "pending_approval", "awaiting_customer", "needs_review"})
        )
    elif ticket_status and ticket_status != "all":
        statement = statement.where(Ticket.status == ticket_status)
    if q:
        statement = statement.where(or_(Ticket.ticket_no.contains(q, autoescape=True), Ticket.legacy_ticket_no.contains(q, autoescape=True), Ticket.subject.contains(q, autoescape=True), Ticket.customer_name.contains(q, autoescape=True)))
    tickets = list(session.scalars(statement).all())
    return tickets if case_type == "all" else [ticket for ticket in tickets if ticket.case_type == case_type]


@router.post(
    "/tickets", response_model=TicketDetail, status_code=status.HTTP_201_CREATED, tags=["tickets"]
)
def create_ticket(payload: TicketCreate, session: Session = Depends(get_db)) -> TicketDetail:
    ticket_data = payload.model_dump()
    for field in ("customer_name", "subject", "message"):
        ticket_data[field] = redact_text(ticket_data[field])
    ticket = Ticket(ticket_no=next_number(session), **ticket_data)
    session.add(ticket)
    attach_ticket_access(ticket, session)
    session.commit()
    session.refresh(ticket)
    return ticket_to_detail(session, ticket)


@router.post(
    "/customer/tickets",
    response_model=CustomerTicketOut,
    status_code=status.HTTP_201_CREATED,
    tags=["customer"],
)
async def create_customer_ticket(
    payload: CustomerTicketCreate,
    session: Session = Depends(get_db),
) -> CustomerTicketOut:
    created_at = utc_now()
    initial_message = redact_text(payload.message.strip())
    if not initial_message:
        raise HTTPException(status_code=422, detail="消息内容不能为空")
    from app.services.reply_validation import extract_order_identifier, requests_money_operation
    customer_name = current_actor().display_name if get_settings().auth_enabled else (payload.customer_name or "客户")
    subject = redact_text((payload.subject or initial_message[:60]).strip())
    order_id = payload.order_id.strip() if payload.order_id else extract_order_identifier(initial_message)
    amount = payload.amount
    if amount is None and requests_money_operation({"subject": subject, "message": initial_message}):
        requested = re.search(r"(?:退款|退还|返还|退回|退给我)\s*(?:金额为|金额是|金额)?\s*(\d+(?:\.\d{1,2})?)\s*元", initial_message)
        if requested and float(requested.group(1)) <= 10000:
            amount = float(requested.group(1))
    ticket = Ticket(
        ticket_no=next_number(session),
        customer_name=redact_text(customer_name.strip()),
        customer_tier="standard",
        channel="web",
        subject=subject,
        message=initial_message,
        priority="medium",
        order_id=order_id,
        amount=amount,
        assessment={
            "conversation": [
                {
                    "id": new_id(),
                    "role": "customer",
                    "content": initial_message,
                    "created_at": created_at.isoformat(),
                }
            ]
        },
        created_at=created_at,
        updated_at=created_at,
    )
    session.add(ticket)
    attach_ticket_access(ticket, session)
    session.commit()
    session.refresh(ticket)
    row = begin_question(session, ticket)
    await answer_question(session, ticket, row)
    return ticket_to_customer_view(ticket)


@router.get(
    "/customer/tickets/{ticket_id}",
    response_model=CustomerTicketOut,
    tags=["customer"],
)
def get_customer_ticket(
    ticket_id: str,
    session: Session = Depends(get_db),
) -> CustomerTicketOut:
    return ticket_to_customer_view(get_customer_ticket_or_404(ticket_id, session))


@router.delete("/customer/tickets/{ticket_id}", tags=["customer"])
def delete_customer_chat(ticket_id: str, session: Session = Depends(get_db)) -> dict:
    # Ownership is checked even on retries. Removing client visibility must never
    # delete the ticket, alter a pending approval, or remove merchant-side evidence.
    assert_ticket_access(ticket_id, session, customer_action=True)
    ticket = get_ticket_or_404(ticket_id, session)
    access = session.get(TicketAccess, ticket.id)
    actor = current_actor()
    if access is None or access.owner_id != actor.id or access.workspace_id != actor.workspace_id:
        raise HTTPException(status_code=404, detail="对话不存在")
    session.execute(update(TicketAccess).where(
        TicketAccess.ticket_id == ticket.id,
        TicketAccess.owner_id == actor.id,
        TicketAccess.workspace_id == actor.workspace_id,
        TicketAccess.customer_deleted_at.is_(None),
    ).values(customer_deleted_at=utc_now()))
    session.commit()
    return {"id": ticket.id, "deleted": True}


@router.post(
    "/customer/tickets/{ticket_id}/messages",
    response_model=CustomerTicketOut,
    tags=["customer"],
)
async def send_customer_message(
    ticket_id: str,
    payload: CustomerMessageRequest,
    session: Session = Depends(get_db),
) -> CustomerTicketOut:
    ticket = get_customer_ticket_or_404(ticket_id, session)
    assessment = dict(ticket.assessment or {})
    confirmation = assessment.get("customer_confirmation") or {}
    if confirmation.get("resolved"):
        raise HTTPException(status_code=409, detail="该咨询已经完成，请发起新的咨询")
    if session.scalar(select(QuestionRoute).where(QuestionRoute.ticket_id == ticket.id, QuestionRoute.outcome == "processing")):
        raise HTTPException(409, detail="上一轮问题正在处理，请稍后再发送")

    content = redact_text(payload.message.strip())
    if not content:
        raise HTTPException(status_code=422, detail="消息内容不能为空")
    sent_at = utc_now()
    # Claim this conversation version before writing its JSON history. Concurrent
    # requests must not overwrite one another or launch two checkpoints for one turn.
    claimed = session.execute(update(Ticket).where(Ticket.id == ticket.id, Ticket.updated_at == ticket.updated_at)
                              .values(updated_at=sent_at))
    if claimed.rowcount != 1:
        session.rollback()
        raise HTTPException(409, detail="会话刚刚发生变化，请刷新后重新发送")
    messages = ticket_conversation(ticket)
    messages.append(
        {
            "id": new_id(),
            "role": "customer",
            "content": content,
            "created_at": sent_at.isoformat(),
        }
    )
    assessment["conversation"] = messages
    assessment.pop("customer_confirmation", None)
    ticket.assessment = assessment
    ticket.message = content
    from app.services.reply_validation import extract_order_identifier, requests_money_operation

    supplied_order = extract_order_identifier(content)
    if supplied_order:
        ticket.order_id = supplied_order
    if requests_money_operation({"subject": ticket.subject, "message": content}):
        requested_amount = re.search(r"(?:退款|退还|返还|退回|退给我)\s*(?:多扣的|金额为|金额是|金额)?\s*(\d+(?:\.\d{1,2})?)\s*元", content)
        if requested_amount and float(requested_amount.group(1)) <= 10000:
            ticket.amount = float(requested_amount.group(1))
    ticket.status = "in_progress"
    ticket.draft_response = None
    handed_off = assessment.get("handoff", {}).get("active", False)
    if not handed_off:
        ticket.requires_approval = False
    checkpoint = session.scalar(
        select(WorkflowCheckpoint).where(WorkflowCheckpoint.ticket_id == ticket.id)
    )
    if checkpoint and checkpoint.status != "superseded" and not handed_off:
        checkpoint.status = "superseded"
        checkpoint.next_node = None
        checkpoint.updated_at = sent_at
    approval = session.scalar(select(Approval).where(Approval.ticket_id == ticket.id))
    if approval and approval.state in {"pending", "approved"}:
        approval.state = "cancelled"
        approval.reviewer_note = "客户补充了新信息，原审批已失效"
        approval.reviewed_at = sent_at
        ticket.requires_approval = False
        if checkpoint:
            checkpoint.status = "superseded"
            checkpoint.next_node = None
    session.add(
        AuditEvent(
            ticket_id=ticket.id,
            agent="客户",
            action="发送追问",
            summary="客户补充了新的问题信息",
            details={"message": content, "round": len(messages)},
        )
    )
    row = begin_question(session, ticket)
    session.refresh(ticket)
    await answer_question(session, ticket, row)
    return ticket_to_customer_view(ticket)


@router.post(
    "/customer/tickets/{ticket_id}/confirmation",
    response_model=CustomerTicketOut,
    tags=["customer"],
)
def confirm_customer_ticket(
    ticket_id: str,
    payload: CustomerConfirmationRequest,
    session: Session = Depends(get_db),
) -> CustomerTicketOut:
    ticket = get_customer_ticket_or_404(ticket_id, session)
    assessment = dict(ticket.assessment or {})
    messages = ticket_conversation(ticket)
    if not messages or messages[-1].get("role") != "support":
        raise HTTPException(status_code=409, detail="客服尚未回复，暂时不能确认处理结果")

    confirmed_at = utc_now()
    feedback = redact_text(payload.feedback.strip())
    assessment["customer_confirmation"] = {
        "resolved": payload.resolved,
        "rating": payload.rating,
        "feedback": feedback,
        "confirmed_at": confirmed_at.isoformat(),
    }
    if not payload.resolved:
        history = list(assessment.get("customer_feedback_history", []))
        history.append({"feedback": feedback or "客户反馈问题仍未解决", "created_at": confirmed_at.isoformat()})
        assessment["customer_feedback_history"] = history
    ticket.assessment = assessment
    ticket.status = "resolved" if payload.resolved else "needs_review"
    if payload.resolved:
        assessment["handoff"] = {"active": False, "reasons": []}
        ticket.assessment = assessment
    if payload.resolved and not ticket.resolution:
        ticket.resolution = "客户确认问题已解决"
    session.add(
        AuditEvent(
            ticket_id=ticket.id,
            agent="客户",
            action="确认处理结果",
            summary="客户确认问题已解决" if payload.resolved else "客户反馈问题仍需处理",
            details={
                "resolved": payload.resolved,
                "rating": payload.rating,
                "feedback": feedback,
            },
        )
    )
    session.commit()
    session.refresh(ticket)
    return ticket_to_customer_view(ticket)


@router.get("/tickets/{ticket_id}", response_model=TicketDetail, tags=["tickets"])
def get_ticket(ticket_id: str, session: Session = Depends(get_db)) -> TicketDetail:
    return ticket_to_detail(session, get_ticket_or_404(ticket_id, session))


@router.post("/tickets/{ticket_id}/analyze", response_model=TicketDetail, tags=["agents"])
async def analyze_ticket(ticket_id: str, session: Session = Depends(get_db)) -> TicketDetail:
    ticket = get_ticket_or_404(ticket_id, session)
    try:
        return await WorkflowService(session).analyze(ticket)
    except Exception as error:
        session.rollback()
        raise HTTPException(status_code=500, detail=f"Agent 工作流执行失败：{error}") from error


@router.post(
    "/tasks/agent-analysis/{ticket_id}",
    response_model=BackgroundTaskOut,
    status_code=status.HTTP_202_ACCEPTED,
    tags=["tasks"],
)
def enqueue_agent_analysis(
    ticket_id: str,
    session: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
    tasks: TaskQueueService = Depends(get_task_queue_service),
) -> dict:
    get_ticket_or_404(ticket_id, session)
    try:
        snapshot = tasks.enqueue_agent_analysis(ticket_id)
    except TaskQueueUnavailableError as error:
        raise HTTPException(status_code=503, detail=str(error)) from error
    attach_task_access(snapshot["id"], session)
    return _task_response(snapshot, settings)


@router.post(
    "/tasks/knowledge-import",
    response_model=BackgroundTaskOut,
    status_code=status.HTTP_202_ACCEPTED,
    tags=["tasks"],
)
def enqueue_knowledge_import(
    payload: KnowledgeImportRequest,
    settings: Settings = Depends(get_settings),
    tasks: TaskQueueService = Depends(get_task_queue_service),
    session: Session = Depends(get_db),
) -> dict:
    try:
        documents = [document.model_dump() for document in payload.documents]
        snapshot = (tasks.enqueue_knowledge_import(documents, workspace_id=current_actor().workspace_id)
                    if settings.auth_enabled else tasks.enqueue_knowledge_import(documents))
    except TaskQueueUnavailableError as error:
        raise HTTPException(status_code=503, detail=str(error)) from error
    attach_task_access(snapshot["id"], session)
    return _task_response(snapshot, settings)


@router.post(
    "/tasks/evaluation",
    response_model=BackgroundTaskOut,
    status_code=status.HTTP_202_ACCEPTED,
    tags=["tasks"],
)
def enqueue_batch_evaluation(
    payload: BatchEvaluationRequest,
    settings: Settings = Depends(get_settings),
    tasks: TaskQueueService = Depends(get_task_queue_service),
    session: Session = Depends(get_db),
) -> dict:
    try:
        snapshot = tasks.enqueue_evaluation(payload.limit)
    except TaskQueueUnavailableError as error:
        raise HTTPException(status_code=503, detail=str(error)) from error
    attach_task_access(snapshot["id"], session)
    return _task_response(snapshot, settings)


@router.get(
    "/tasks/{task_id}",
    response_model=BackgroundTaskOut,
    tags=["tasks"],
)
def get_background_task(
    task_id: str,
    settings: Settings = Depends(get_settings),
    tasks: TaskQueueService = Depends(get_task_queue_service),
    session: Session = Depends(get_db),
) -> dict:
    assert_task_access(task_id, session)
    return _task_response(_get_task_snapshot(tasks, task_id), settings)


@router.get("/tasks/{task_id}/events", tags=["tasks"])
async def stream_background_task(
    task_id: str,
    request: Request,
    settings: Settings = Depends(get_settings),
    tasks: TaskQueueService = Depends(get_task_queue_service),
    session: Session = Depends(get_db),
) -> StreamingResponse:
    assert_task_access(task_id, session)
    # Ownership is checked before opening SSE; don't hold a SQL connection while streaming.
    session.close()
    initial = _task_response(_get_task_snapshot(tasks, task_id), settings)

    async def events():
        snapshot = initial
        last_revision: tuple | None = None
        heartbeat_ticks = 0
        while True:
            revision = (
                snapshot["status"],
                snapshot["progress"],
                snapshot["stage"],
                snapshot["message"],
                snapshot.get("updated_at"),
            )
            if revision != last_revision:
                if snapshot["status"] == "finished":
                    event = "completed"
                elif snapshot["status"] in {"failed", "stopped", "canceled"}:
                    event = "failed"
                else:
                    event = "progress"
                yield _encode_sse(event, snapshot)
                last_revision = revision
                heartbeat_ticks = 0
            if snapshot["status"] in TERMINAL_TASK_STATUSES:
                break
            if await request.is_disconnected():
                break
            await asyncio.sleep(0.5)
            heartbeat_ticks += 1
            if heartbeat_ticks >= 30:
                yield ": keep-alive\n\n"
                heartbeat_ticks = 0
            try:
                snapshot = _task_response(
                    await run_in_threadpool(tasks.snapshot, task_id),
                    settings,
                )
            except (BackgroundTaskNotFoundError, TaskQueueUnavailableError):
                break

    return StreamingResponse(
        events(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


@router.post("/tickets/{ticket_id}/decision", response_model=TicketDetail, tags=["approvals"])
async def decide_ticket(
    ticket_id: str,
    payload: DecisionRequest,
    session: Session = Depends(get_db),
) -> TicketDetail:
    if get_settings().auth_enabled:
        payload = payload.model_copy(update={"reviewer": current_actor().username})
    ticket = get_ticket_or_404(ticket_id, session)
    approval = session.scalar(select(Approval).where(Approval.ticket_id == ticket.id))
    if not approval:
        raise HTTPException(status_code=409, detail="该工单没有待审批动作")
    expected_state = "approved" if payload.decision == "approve" else "rejected"
    checkpoint = session.scalar(
        select(WorkflowCheckpoint).where(WorkflowCheckpoint.ticket_id == ticket.id)
    )
    if approval.state == "pending":
        approval.state = expected_state
        approval.reviewer_note = redact_text(payload.note)
        approval.reviewed_by = redact_text(payload.reviewer)
        approval.reviewed_at = utc_now()
        session.add(
            AuditEvent(
                ticket_id=ticket.id,
                agent="人工审批",
                action="审批高风险动作",
                summary=(
                    f"{payload.reviewer}已"
                    f"{('批准' if payload.decision == 'approve' else '驳回')}方案"
                ),
                details={"decision": payload.decision, "note": redact_text(payload.note)},
            )
        )
        session.commit()
    elif not (
        checkpoint
        and checkpoint.status == "waiting_approval"
        and approval.state == expected_state
    ):
        raise HTTPException(status_code=409, detail="该审批已经处理")

    if checkpoint and checkpoint.status == "waiting_approval":
        try:
            return await WorkflowService(session).resume_after_approval(
                ticket,
                decision=payload.decision,
                reviewer=approval.reviewed_by or redact_text(payload.reviewer),
                note=approval.reviewer_note or redact_text(payload.note),
            )
        except Exception as error:
            session.rollback()
            raise HTTPException(
                status_code=500,
                detail=f"审批已保存，但 Agent Checkpoint 恢复失败：{error}",
            ) from error

    # Compatibility path for approvals created before persistent checkpoints existed.
    if payload.decision == "approve":
        messages = ticket_conversation(ticket)
        ticket.status = (
            "awaiting_customer"
            if messages and messages[-1].get("role") == "support"
            else "in_progress"
        )
        ticket.resolution = approval.action
    else:
        ticket.status = "needs_review"
        ticket.resolution = "人工驳回，转交客服复核"
    ticket.requires_approval = False
    session.commit()
    session.refresh(ticket)
    return ticket_to_detail(session, ticket)


@router.post("/tickets/{ticket_id}/draft/rewrite", response_model=TicketDetail, tags=["agents"])
async def rewrite_ticket_draft(
    ticket_id: str,
    payload: DraftRewriteRequest,
    session: Session = Depends(get_db),
) -> TicketDetail:
    ticket = get_ticket_or_404(ticket_id, session)
    if not ticket.draft_response:
        raise HTTPException(status_code=409, detail="请先运行 Agent 生成回复草稿")

    llm = LLMService()
    from app.services.metering import persist_usage
    llm.usage_recorder = lambda metadata: persist_usage(session.bind, ticket.id, metadata)
    facts = {
            "ticket_no": ticket.ticket_no,
            "customer_name": ticket.customer_name,
            "subject": ticket.subject,
            "order_id": ticket.order_id,
            "amount": ticket.amount,
            "category": ticket.category,
            "requires_approval": ticket.requires_approval,
            "approval_state": (
                session.scalar(select(Approval).where(Approval.ticket_id == ticket.id)).state
                if session.scalar(select(Approval).where(Approval.ticket_id == ticket.id)) else "not_required"
            ),
            "completed_operations": [
                {"tool_name": item.tool_name, "result": item.result}
                for item in session.scalars(select(ToolExecution).where(
                    ToolExecution.ticket_id == ticket.id, ToolExecution.status == "completed",
                )).all()
            ],
        }
    instruction = redact_text(payload.instruction)
    rewrite_traces = []
    for attempt in range(get_settings().quality_max_regenerations + 1):
        rewritten = await run_in_threadpool(llm.rewrite, facts, ticket.draft_response, instruction)
        rewrite_traces.append({"stage": "rewrite", "quality_attempt": attempt + 1, **llm.call_metadata.get("rewrite", {})})
        try:
            validate_reply_before_send(ticket, session, redact_text(rewritten))
            break
        except HTTPException as error:
            if attempt >= get_settings().quality_max_regenerations:
                session.add(AuditEvent(ticket_id=ticket.id, agent="回复事实校验", action="拒绝不可靠改写",
                                       summary="原草稿保留；有限重生成后仍未通过校验", details={"failed_checks": error.detail, "attempts": attempt + 1}))
                session.commit()
                raise
            instruction += "；上一轮未通过事实校验，请删除没有工具或知识文档依据的行动、路径和时效承诺。"
    ticket.draft_response = redact_text(rewritten)
    assessment = dict(ticket.assessment or {})
    model_trace = list(assessment.get("model_trace", []))
    model_trace.extend(rewrite_traces)
    assessment["model_trace"] = model_trace
    ticket.assessment = assessment
    session.add(
        AuditEvent(
            ticket_id=ticket.id,
            agent="回复优化 Agent",
            action="改写客服回复",
            summary="已根据原方案生成新的客户回复草稿",
            details={
                "instruction": redact_text(payload.instruction),
                **llm.call_metadata.get("rewrite", {}),
            },
        )
    )
    session.commit()
    session.refresh(ticket)
    return ticket_to_detail(session, ticket)


@router.post("/tickets/{ticket_id}/draft/send", response_model=TicketDetail, tags=["tickets"])
def send_ticket_response(
    ticket_id: str,
    payload: DraftSendRequest,
    session: Session = Depends(get_db),
) -> TicketDetail:
    ticket = get_ticket_or_404(ticket_id, session)
    response_text = redact_text(payload.response.strip())
    if not response_text:
        raise HTTPException(status_code=422, detail="回复内容不能为空")
    reply_checks = validate_reply_before_send(ticket, session, response_text)

    sent_at = utc_now()
    assessment = dict(ticket.assessment or {})
    messages = ticket_conversation(ticket)
    messages.append(
        {
            "id": new_id(),
            "role": "support",
            "content": response_text,
            "created_at": sent_at.isoformat(),
        }
    )
    assessment["sent_response"] = response_text
    assessment["sent_at"] = sent_at.isoformat()
    assessment["conversation"] = messages
    assessment.pop("customer_confirmation", None)
    ticket.assessment = assessment
    ticket.draft_response = None
    if ticket.status != "pending_approval":
        ticket.status = "awaiting_customer"
    session.add(
        AuditEvent(
            ticket_id=ticket.id,
            agent="客服坐席",
            action="发送客户回复",
            summary=f"已通过{ticket.channel}演示通道发送回复",
            details={"channel": ticket.channel, "response": response_text, "mode": "simulated", "reply_checks": reply_checks,
                     "actor": current_actor().username},
        )
    )
    pending_routes = session.scalars(select(QuestionRoute).where(
        QuestionRoute.ticket_id == ticket.id,
        QuestionRoute.outcome.in_(["handed_off", "waiting_human", "failed_to_human", "processing"]))).all()
    for route_record in pending_routes:
        route_record.outcome = "human_replied"
        route_record.finished_at = sent_at
        route_record.steps = list(route_record.steps or []) + [{"agent": "人工客服", "action": "发送客户回复",
            "summary": "客服已回复；人工等待时间不计入自动处理耗时", "duration_ms": 0, "status": "completed"}]
    session.commit()
    session.refresh(ticket)
    return ticket_to_detail(session, ticket)


@router.get("/knowledge/search", tags=["knowledge"])
def search_knowledge(q: str, session: Session = Depends(get_db)) -> dict:
    safe_query = redact_text(q)
    service = RetrievalService(session, workspace_id=current_actor().workspace_id)
    results = service.search(safe_query, limit=5)
    return {"query": safe_query, "results": results, "cache_hit": service.last_cache_hit}


@router.get("/performance/metrics", tags=["performance"])
def performance_metrics(session: Session = Depends(get_db)) -> dict:
    return build_performance_report(session)


@router.get(
    "/knowledge/documents",
    response_model=list[KnowledgeDocumentOut],
    tags=["knowledge"],
)
def list_knowledge_documents(session: Session = Depends(get_db)) -> list[KnowledgeDocument]:
    statement = select(KnowledgeDocument).where(knowledge_scope(current_actor().workspace_id)).order_by(KnowledgeDocument.created_at.desc())
    return list(session.scalars(statement).all())


@router.get(
    "/knowledge/documents/{document_id}",
    response_model=KnowledgeDocumentOut,
    tags=["knowledge"],
)
def get_knowledge_document(
    document_id: str,
    session: Session = Depends(get_db),
) -> KnowledgeDocument:
    document = session.scalar(select(KnowledgeDocument).where(KnowledgeDocument.id == document_id, knowledge_scope(current_actor().workspace_id)))
    if not document:
        raise HTTPException(status_code=404, detail="知识文档不存在")
    return document


@router.post("/knowledge/documents", response_model=KnowledgeDocumentOut, status_code=201, tags=["knowledge"])
def add_faq(payload: KnowledgeImportItem, session: Session = Depends(get_db)):
    from app.services.auth import require_role
    from app.services.retrieval import invalidate_retrieval_cache
    actor = require_role("admin")
    embeddings = EmbeddingService()
    document = KnowledgeDocument(**payload.model_dump(), embedding=embeddings.legacy_embed(f"{payload.title}\n{payload.content}"))
    session.add(document)
    session.flush()
    session.add(KnowledgeAccess(document_id=document.id, workspace_id=actor.workspace_id))
    session.commit()
    invalidate_retrieval_cache()
    return document


@router.patch("/knowledge/documents/{document_id}", response_model=KnowledgeDocumentOut, tags=["knowledge"])
def toggle_knowledge(document_id: str, payload: KnowledgeToggle, session: Session = Depends(get_db)):
    from app.services.auth import require_role
    from app.services.retrieval import invalidate_retrieval_cache
    actor = require_role("admin")
    document = get_knowledge_document(document_id, session)
    access = session.get(KnowledgeAccess, document_id)
    if access is None and actor.workspace_id != "default":
        raise HTTPException(403, "公共文档只允许默认工作空间维护")
    document.enabled = payload.enabled
    session.commit()
    invalidate_retrieval_cache()
    return document


@router.get("/questions/routes", tags=["questions"])
def question_routes(q: str = Query(default="", max_length=100), limit: int = Query(default=100, ge=1, le=500),
                    offset: int = Query(default=0, ge=0), session: Session = Depends(get_db)):
    stmt = select(QuestionRoute, Ticket.ticket_no).join(Ticket, Ticket.id == QuestionRoute.ticket_id).where(ticket_scope())
    if q:
        stmt = stmt.where(or_(QuestionRoute.question.contains(q, autoescape=True), Ticket.ticket_no.contains(q, autoescape=True), Ticket.legacy_ticket_no.contains(q, autoescape=True)))
    return [{**{c.name: getattr(row, c.name) for c in QuestionRoute.__table__.columns}, "ticket_no": number}
            for row, number in session.execute(stmt.order_by(QuestionRoute.request_time.desc()).offset(offset).limit(limit)).all()]


@router.get("/customer/tickets", response_model=list[CustomerTicketOut], tags=["customer"])
def customer_ticket_history(session: Session = Depends(get_db)):
    actor = current_actor()
    stmt = select(Ticket).join(TicketAccess, TicketAccess.ticket_id == Ticket.id).where(
        TicketAccess.owner_id == actor.id, TicketAccess.workspace_id == actor.workspace_id,
        TicketAccess.customer_deleted_at.is_(None))
    return [ticket_to_customer_view(t) for t in session.scalars(stmt.order_by(Ticket.created_at.desc())).all()]


@router.get("/agents/topology", tags=["agents"])
def agent_topology() -> dict:
    return {"edges": graph_topology()}


@router.get("/agents/runs", response_model=list[AgentRunOut], tags=["agents"])
def agent_runs(
    limit: int = Query(default=30, ge=1, le=100),
    session: Session = Depends(get_db),
) -> list[AgentRunOut]:
    statement = (
        select(AuditEvent, Ticket.ticket_no)
        .join(Ticket, Ticket.id == AuditEvent.ticket_id)
        .where(ticket_scope())
        .order_by(AuditEvent.created_at.desc())
        .limit(limit)
    )
    return [
        AgentRunOut(
            id=event.id,
            ticket_id=event.ticket_id,
            ticket_no=ticket_no,
            agent=event.agent,
            action=event.action,
            summary=event.summary,
            status=event.status,
            duration_ms=event.duration_ms,
            created_at=event.created_at,
        )
        for event, ticket_no in session.execute(statement).all()
    ]
