from time import perf_counter
from uuid import uuid4

from langgraph.types import Command
from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from app.agents.graph import build_support_graph
from app.models import Approval, AuditEvent, Ticket, TicketAccess, ToolExecution, WorkflowCheckpoint, utc_now
from app.schemas import Citation, TicketDetail
from app.services.checkpoint import SQLAlchemyCheckpointSaver
from app.services.confidence import calculate_confidence
from app.core.observability import current_trace_id, trace_url, traced_workflow
from app.services.security import redact_conversation, redact_text
from app.services.metering import meter_workflow


class WorkflowService:
    def __init__(
        self,
        session: Session,
        *,
        graph_dependencies: dict | None = None,
    ) -> None:
        self.session = session
        self.graph_dependencies = graph_dependencies or {}
        self.submitted_at = None
        self.task_id = None

    @traced_workflow("analyze")
    @meter_workflow("analyze")
    async def analyze(self, ticket: Ticket) -> TicketDetail:
        workflow_started = perf_counter()
        previous_assessment = dict(ticket.assessment or {})
        conversation = ticket_conversation(ticket)
        safe_conversation = redact_conversation(conversation)
        checkpoint = self.session.scalar(
            select(WorkflowCheckpoint).where(WorkflowCheckpoint.ticket_id == ticket.id)
        )
        if checkpoint and checkpoint.status == "waiting_approval":
            return ticket_to_detail(self.session, ticket)

        initial_state = None
        if checkpoint is None or checkpoint.status != "running":
            checkpoint = self._start_checkpoint(ticket, safe_conversation, checkpoint)
            self.session.execute(delete(Approval).where(Approval.ticket_id == ticket.id))
            self.session.commit()
            initial_state = {
                "ticket": {
                    "id": ticket.id,
                    "workspace_id": (self.session.get(TicketAccess, ticket.id).workspace_id
                                     if self.session.get(TicketAccess, ticket.id) else "default"),
                    "ticket_no": ticket.ticket_no,
                    "customer_name": redact_text(ticket.customer_name),
                    "customer_tier": ticket.customer_tier,
                    "subject": redact_text(ticket.subject),
                    "message": redact_text(ticket.message),
                    "priority": ticket.priority,
                    "category": ticket.category,
                    "order_id": ticket.order_id,
                    "amount": ticket.amount,
                    "completed_operations": [
                        {"tool_name": item.tool_name, "result": item.result}
                        for item in self.session.scalars(select(ToolExecution).where(
                            ToolExecution.ticket_id == ticket.id, ToolExecution.status == "completed",
                        )).all()
                    ],
                },
                "conversation": safe_conversation,
                "model_trace": [],
                "audit": [],
                "errors": [],
                "failures": [],
                "generation_attempt": 0,
            }
        graph = build_support_graph(
            self.session,
            checkpointer=SQLAlchemyCheckpointSaver(),
            **self.graph_dependencies,
        )
        config = {"configurable": {"thread_id": checkpoint.thread_id}}
        state = await graph.ainvoke(initial_state, config)
        snapshot = await graph.aget_state(config)
        self.session.refresh(checkpoint)
        waiting_for_approval = _snapshot_has_interrupt(snapshot)
        return self._persist_state(
            ticket,
            checkpoint,
            state,
            previous_assessment=previous_assessment,
            waiting_for_approval=waiting_for_approval,
            workflow_duration_ms=round((perf_counter() - workflow_started) * 1000, 3),
        )

    @traced_workflow("resume_after_approval")
    @meter_workflow("resume_after_approval")
    async def resume_after_approval(
        self,
        ticket: Ticket,
        *,
        decision: str,
        reviewer: str,
        note: str,
    ) -> TicketDetail:
        workflow_started = perf_counter()
        checkpoint = self.session.scalar(
            select(WorkflowCheckpoint).where(WorkflowCheckpoint.ticket_id == ticket.id)
        )
        if checkpoint is None or checkpoint.status != "waiting_approval":
            raise ValueError("该工单没有可恢复的人工审批 Checkpoint")

        previous_assessment = dict(ticket.assessment or {})
        graph = build_support_graph(
            self.session,
            checkpointer=SQLAlchemyCheckpointSaver(),
            **self.graph_dependencies,
        )
        config = {"configurable": {"thread_id": checkpoint.thread_id}}
        state = await graph.ainvoke(
            Command(
                update={
                    "conversation": redact_conversation(ticket_conversation(ticket))
                },
                resume={
                    "decision": decision,
                    "reviewer": reviewer,
                    "note": note,
                }
            ),
            config,
        )
        snapshot = await graph.aget_state(config)
        if _snapshot_has_interrupt(snapshot):
            raise RuntimeError("审批已提交，但 Agent 工作流仍处于中断状态")
        self.session.refresh(checkpoint)
        checkpoint.resumed_at = utc_now()
        return self._persist_state(
            ticket,
            checkpoint,
            state,
            previous_assessment=previous_assessment,
            waiting_for_approval=False,
            decision=decision,
            workflow_duration_ms=round((perf_counter() - workflow_started) * 1000, 3),
        )

    def _start_checkpoint(
        self,
        ticket: Ticket,
        conversation: list[dict],
        checkpoint: WorkflowCheckpoint | None,
    ) -> WorkflowCheckpoint:
        thread_id = f"{ticket.id}:{uuid4().hex}"
        if checkpoint is None:
            checkpoint = WorkflowCheckpoint(
                ticket_id=ticket.id,
                thread_id=thread_id,
            )
            self.session.add(checkpoint)
        else:
            checkpoint.thread_id = thread_id
            checkpoint.checkpoint_id = None
            checkpoint.current_node = "START"
            checkpoint.next_node = "triage_agent"
            checkpoint.status = "running"
            checkpoint.state = {}
            checkpoint.tool_results = []
            checkpoint.audit_count = 0
            checkpoint.resumed_at = None
            checkpoint.completed_at = None
        checkpoint.conversation = conversation
        checkpoint.updated_at = utc_now()
        self.session.commit()
        self.session.refresh(checkpoint)
        return checkpoint

    def _persist_state(
        self,
        ticket: Ticket,
        checkpoint: WorkflowCheckpoint,
        state: dict,
        *,
        previous_assessment: dict,
        waiting_for_approval: bool,
        decision: str | None = None,
        workflow_duration_ms: float = 0.0,
    ) -> TicketDetail:
        triage = state["triage"]
        resolution = state["resolution"]
        citations = state.get("citations", [])
        model_trace = state.get("model_trace", [])
        confidence_breakdown = calculate_confidence(
            triage=triage,
            citations=citations,
            customer_context=state["customer_context"],
            quality=state["quality"],
            resolution=resolution,
            model_trace=model_trace,
        )
        failures = state.get("failures", [])
        resilience = {
            "failures": failures,
            "failure_count": len(failures),
            "model_retry_counts": {
                str(item.get("stage", "unknown")): int(item.get("retry_count", 0))
                for item in model_trace
            },
            "tool_retry_count": int(
                state.get("customer_context", {}).get("tool_retry_count", 0)
            ),
            "rag": state.get("rag_status", {}),
            "quality_regeneration_count": int(
                state.get("quality", {}).get("regeneration_count", 0)
            ),
            "escalated_to_human": waiting_for_approval,
        }
        ticket.category = triage["category"]
        ticket.priority = triage["priority"]
        ticket.sentiment = triage["sentiment"]
        ticket.confidence = confidence_breakdown["final_score"]
        ticket.customer_name = redact_text(ticket.customer_name)
        ticket.subject = redact_text(ticket.subject)
        ticket.message = redact_text(ticket.message)
        ticket.recommended_action = redact_text(resolution["recommended_action"])
        messages = ticket_conversation(ticket)
        ticket.draft_response = (
            None
            if decision and previous_assessment.get("sent_response")
            and messages and messages[-1].get("role") == "support"
            else redact_text(resolution["draft_response"])
        )
        ticket.requires_approval = resolution["requires_approval"]
        active_trace_id = current_trace_id()
        previous_observability = dict(previous_assessment.get("observability") or {})
        trace_history = list(previous_observability.get("trace_history") or [])
        previous_trace_id = previous_observability.get("trace_id")
        if previous_trace_id and previous_trace_id != active_trace_id:
            trace_history.append(previous_trace_id)
        observability = {
            "provider": "opentelemetry",
            "trace_id": active_trace_id,
            "trace_url": trace_url(active_trace_id),
            "trace_history": list(dict.fromkeys(trace_history))[-10:],
            "workflow_duration_ms": workflow_duration_ms,
        }
        ticket.assessment = {
            "intent": triage["intent"],
            "rationale": resolution["rationale"],
            "refund_amount": resolution.get("refund_amount"),
            "resolution": {
                **resolution,
                "draft_response": redact_text(resolution.get("draft_response")),
                "rationale": redact_text(resolution.get("rationale")),
            },
            "customer_context": state["customer_context"],
            "policy": state["policy"],
            "security": state.get("security", {}),
            "quality": state["quality"],
            "gate": state.get("gate") or {"state": "pending"},
            "model_trace": model_trace,
            "citations": citations,
            "confidence_breakdown": confidence_breakdown,
            "resilience": resilience,
            "observability": observability,
            "conversation": redact_conversation(
                previous_assessment.get("conversation")
                or state.get("conversation")
                or ticket_conversation(ticket)
            ),
        }
        for key in ("sent_response", "sent_at"):
            if previous_assessment.get(key):
                ticket.assessment[key] = (
                    redact_text(previous_assessment[key])
                    if key == "sent_response"
                    else previous_assessment[key]
                )
        ticket.requires_approval = waiting_for_approval
        if waiting_for_approval:
            ticket.status = "pending_approval"
        elif decision == "reject":
            ticket.status = "needs_review"
            ticket.resolution = "人工驳回，转交客服复核"
        elif decision == "approve":
            messages = ticket_conversation(ticket)
            ticket.status = (
                "awaiting_customer"
                if messages and messages[-1].get("role") == "support"
                else "in_progress"
            )
            ticket.resolution = resolution["recommended_action"]
        else:
            ticket.status = "in_progress"
            ticket.resolution = resolution["recommended_action"]

        if waiting_for_approval:
            approval_action = resolution["recommended_action"]
            approval_amount = resolution.get("refund_amount")
            requested_amount = state["ticket"].get("amount")
            verified_paid_amount = state["customer_context"].get("order_amount")
            explicit_money_request = (
                state["policy"].get("financial_action_requested")
                and requested_amount is not None and requested_amount > 0
                and verified_paid_amount is not None and requested_amount <= verified_paid_amount
                and state["customer_context"].get("paid")
                and not state["customer_context"].get("tool_fallback")
                and not state["policy"].get("security_reasons")
                and state["quality"].get("passed")
                and triage["category"] in {"refund", "payment"}
            )
            # Approval scope comes from an explicit, validated request plus paid
            # order facts, not from whether an LLM happens to fill an optional field.
            if approval_amount is None and explicit_money_request:
                approval_amount = requested_amount
            # The reviewer must see the concrete money operation, even when the
            # model's prose only says "人工审批". Do not relabel security/QA reviews.
            if (approval_amount is not None
                    and state["policy"].get("financial_action_requested")
                    and not state["policy"].get("security_reasons")
                    and state["quality"].get("passed")
                    and triage["category"] in {"refund", "payment"}):
                approval_action = f"退款 {approval_amount:g} 元：{approval_action}"
            approval = self.session.scalar(
                select(Approval).where(Approval.ticket_id == ticket.id)
            )
            if approval is None:
                approval = Approval(
                    ticket_id=ticket.id,
                    state="pending",
                    action=approval_action,
                    amount=approval_amount,
                    risk_reason=_approval_reason(state),
                )
                self.session.add(approval)
            else:
                approval.state = "pending"
                approval.action = approval_action
                approval.amount = approval_amount
                approval.risk_reason = _approval_reason(state)

        state_audit = state.get("audit", [])
        for item in state_audit[checkpoint.audit_count :]:
            self.session.add(AuditEvent(ticket_id=ticket.id, **item))
        checkpoint.audit_count = len(state_audit)
        checkpoint.state = {
            key: value
            for key, value in state.items()
            if not str(key).startswith("__")
        }
        checkpoint.conversation = state.get("conversation") or ticket_conversation(ticket)
        checkpoint.tool_results = (
            [
                {
                    "tool_name": "get_order_context",
                    "result": state["customer_context"],
                }
            ]
            if state.get("customer_context")
            else []
        )
        checkpoint.updated_at = utc_now()
        if waiting_for_approval:
            checkpoint.current_node = "approval_gate"
            checkpoint.next_node = "approval_gate"
            checkpoint.status = "waiting_approval"
            self.session.add(
                AuditEvent(
                    ticket_id=ticket.id,
                    agent="审批门控 Agent",
                    action="保存人工审批 Checkpoint",
                    summary="工作流已持久化并暂停，等待人工审批后恢复",
                    details={"thread_id": checkpoint.thread_id},
                )
            )
        else:
            checkpoint.current_node = "approval_gate"
            checkpoint.next_node = None
            checkpoint.status = "completed"
            checkpoint.completed_at = utc_now()

        self.session.commit()
        self.session.refresh(ticket)
        return ticket_to_detail(self.session, ticket)


def _snapshot_has_interrupt(snapshot) -> bool:
    return any(getattr(task, "interrupts", ()) for task in getattr(snapshot, "tasks", ()))


def _approval_reason(state: dict) -> str:
    security_reasons = state.get("security", {}).get("reasons", [])
    if security_reasons:
        return "安全策略要求人工确认：" + ", ".join(security_reasons)
    failures = state.get("failures", [])
    if failures:
        codes = ", ".join(str(item.get("code", "UNKNOWN_FAILURE")) for item in failures)
        return f"自动流程出现异常或依据不足，需要人工确认：{codes}"
    return "退款金额超过100元或命中风险规则，需要人工确认"


def ticket_to_detail(session: Session, ticket: Ticket) -> TicketDetail:
    events = session.scalars(
        select(AuditEvent)
        .where(AuditEvent.ticket_id == ticket.id)
        .order_by(AuditEvent.created_at.asc())
    ).all()
    approval = session.scalar(select(Approval).where(Approval.ticket_id == ticket.id))
    raw_citations = (ticket.assessment or {}).get("citations", [])
    citations = [Citation.model_validate(item) for item in raw_citations]
    assessment = ticket.assessment or {}
    data = TicketDetail.model_validate(ticket).model_dump()
    data.update(
        citations=citations,
        audit_events=events,
        approval=approval,
        sent_response=assessment.get("sent_response"),
        sent_at=assessment.get("sent_at"),
        conversation=ticket_conversation(ticket),
    )
    return TicketDetail.model_validate(data)


def ticket_conversation(ticket: Ticket) -> list[dict]:
    assessment = ticket.assessment or {}
    stored = assessment.get("conversation")
    if stored:
        return list(stored)

    messages = [
        {
            "id": f"{ticket.id}-initial",
            "role": "customer",
            "content": ticket.message,
            "created_at": ticket.created_at.isoformat(),
        }
    ]
    if assessment.get("sent_response"):
        messages.append(
            {
                "id": f"{ticket.id}-legacy-reply",
                "role": "support",
                "content": assessment["sent_response"],
                "created_at": assessment.get("sent_at") or ticket.updated_at.isoformat(),
            }
        )
    return messages
