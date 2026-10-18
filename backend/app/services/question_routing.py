"""Durable request records. One row is committed before handling each customer turn."""
from time import perf_counter
from sqlalchemy import select

from app.models import QuestionRoute, ModelUsage, WorkflowRun, WorkflowCheckpoint, AuditEvent, new_id, utc_now
from app.services.workflow import WorkflowService, ticket_conversation
from app.services.customer_reply import human_handoff_reply


def begin_question(session, ticket):
    message = ticket_conversation(ticket)[-1]
    row = QuestionRoute(ticket_id=ticket.id, message_id=message["id"], question=message["content"])
    session.add(row)
    session.commit()
    return row


def append_reply(ticket, text, *, human=False):
    assessment = dict(ticket.assessment or {})
    messages = ticket_conversation(ticket)
    messages.append({"id": new_id(), "role": "support", "content": text, "created_at": utc_now().isoformat(),
                     "sender": "human" if human else "agent"})
    assessment.update(conversation=messages, sent_response=text, sent_at=messages[-1]["created_at"])
    assessment.pop("customer_confirmation", None)
    ticket.assessment = assessment
    ticket.draft_response = None
    if not ticket.requires_approval:
        ticket.status = "awaiting_customer"


async def answer_question(session, ticket, row):
    timer = perf_counter()
    previous_handoff = (ticket.assessment or {}).get("handoff", {}).get("active", False)
    try:
        if previous_handoff:
            row.outcome = "waiting_human"
            row.route = "human_queue"
            row.handed_off = True
            row.handoff_reasons = (ticket.assessment or {}).get("handoff", {}).get("reasons", [])
            row.steps = [{"agent": "转人工路由", "duration_ms": 0, "status": "completed", "summary": "后续问题保留在人工会话，不重复自动回复"}]
            ticket.status = "needs_review" if not ticket.requires_approval else "pending_approval"
            return
        service = WorkflowService(session)
        service.submitted_at = row.request_time
        await service.analyze(ticket)
        session.refresh(ticket)
        handoff = (ticket.assessment or {}).get("handoff", {})
        row.handed_off = bool(ticket.requires_approval)
        row.handoff_reasons = handoff.get("reasons", [])
        row.route = "human_queue" if row.handed_off else (ticket.assessment or {}).get("customer_context", {}).get("assistant_route", "agent")
        if row.handed_off:
            row.outcome = "handed_off"
            append_reply(ticket, human_handoff_reply(row.handoff_reasons))
            assessment = dict(ticket.assessment)
            assessment["handoff_ack_id"] = assessment["conversation"][-1]["id"]
            ticket.assessment = assessment
        else:
            from app.api.routes import validate_reply_before_send
            validate_reply_before_send(ticket, session, ticket.draft_response or "")
            append_reply(ticket, ticket.draft_response)
            row.outcome = "replied"
        session.add(AuditEvent(ticket_id=ticket.id, agent="客服路由", action="转人工" if row.handed_off else "自动发送回复",
                               summary="已进入人工队列" if row.handed_off else "低风险回复已自动发送，无需后台审批",
                               details={"question_route_id": row.id}))
        run_id = getattr(service, "last_run_id", None)
        usages = session.scalars(select(ModelUsage).where(ModelUsage.workflow_run_id == run_id)).all() if run_id else []
        row.model_requests = sum(1 for u in usages if u.usage.get("provider") not in {"demo", "information_guard", "policy_guard"})
        context = (ticket.assessment or {}).get("customer_context", {})
        row.tool_requests = int(context.get("tool_http_requests", 0))
        row.api_requests = row.model_requests + row.tool_requests
        events = session.scalars(select(AuditEvent).where(AuditEvent.ticket_id == ticket.id, AuditEvent.created_at >= row.request_time).order_by(AuditEvent.created_at)).all()
        row.steps = [{"agent": e.agent, "action": e.action, "summary": e.summary, "duration_ms": e.duration_ms, "status": e.status} for e in events]
        traces = (ticket.assessment or {}).get("model_trace", [])
        row.degraded = any(t.get("fallback") for t in traces) or bool(context.get("tool_fallback")) or not (ticket.assessment or {}).get("quality", {}).get("passed", True)
    except Exception as error:
        session.rollback()
        session.refresh(ticket)
        row = session.get(QuestionRoute, row.id)
        row.outcome, row.route, row.handed_off, row.degraded = "failed_to_human", "human_queue", True, True
        from app.core.observability import safe_error_message
        row.error = f"{type(error).__name__}: {safe_error_message(error)[:400]}"
        row.handoff_reasons = ["自动处理失败，转人工核验"]
        assessment = dict(ticket.assessment or {})
        assessment["handoff"] = {"active": True, "reasons": row.handoff_reasons}
        ticket.assessment = assessment
        ticket.status = "needs_review"
        append_reply(ticket, "您好，抱歉没能直接解决您的问题，已为您转接人工客服。您可以继续在这里补充情况，无需重复提交。")
        row.steps = [{"agent": "异常降级", "duration_ms": round((perf_counter() - timer) * 1000, 3), "status": "failed", "summary": "自动处理失败，保留对话并转人工"}]
        checkpoint = session.scalar(select(WorkflowCheckpoint).where(WorkflowCheckpoint.ticket_id == ticket.id))
        if checkpoint:
            completed_steps = (checkpoint.state or {}).get("audit", [])
            row.steps = [{"agent": e["agent"], "action": e.get("action"), "duration_ms": e.get("duration_ms", 0),
                          "status": e.get("status", "completed"), "summary": e.get("summary", "")} for e in completed_steps] + row.steps
            checkpoint.status = "superseded"
            checkpoint.next_node = None
        session.add(AuditEvent(ticket_id=ticket.id, agent="异常路由", action="转人工", summary="自动处理失败，问题已持久化", details={"question_route_id": row.id, "error": row.error}))
        runs = session.scalars(select(WorkflowRun.id).where(WorkflowRun.ticket_id == ticket.id, WorkflowRun.submitted_at >= row.request_time)).all()
        if runs:
            usages = session.scalars(select(ModelUsage).where(ModelUsage.workflow_run_id.in_(runs))).all()
            row.model_requests = sum(u.usage.get("provider") != "demo" for u in usages)
            row.api_requests = row.model_requests
    finally:
        row.finished_at = utc_now()
        row.total_ms = round((perf_counter() - timer) * 1000, 3)
        session.add(row)
        session.commit()
