from time import perf_counter
from typing import Any

from langgraph.graph import END, START, StateGraph
from langgraph.types import interrupt
from sqlalchemy.orm import Session

from app.agents.state import SupportState
from app.core.config import get_settings
from app.core.observability import traced_agent_node
from app.services.commerce import CommerceGateway
from app.services.llm import LLMService
from app.services.retrieval import RetrievalService
from app.services.reply_validation import check_draft_facts, requests_money_operation
from app.services.retrieval_query import build_retrieval_query
from app.services.security import SecurityService, redact_conversation, redact_text
from app.services.shop_assistant import catalog_details_reply, needs_order_context, promotion_faq_citation, promotion_information_reply, service_scope_reply, unavailable_catalog_plan_reply


def _event(
    agent: str,
    action: str,
    summary: str,
    started: float,
    *,
    event_status: str = "completed",
    **details: Any,
) -> dict:
    return {
        "agent": agent,
        "action": action,
        "summary": summary,
        "duration_ms": max(1, int((perf_counter() - started) * 1000)),
        "status": event_status,
        "details": details or None,
    }


def build_support_graph(
    session: Session,
    checkpointer=None,
    *,
    llm: LLMService | None = None,
    retrieval: RetrievalService | None = None,
    commerce: CommerceGateway | None = None,
    security: SecurityService | None = None,
):
    settings = get_settings()
    llm = llm or LLMService()
    retrieval = retrieval or RetrievalService(session)
    commerce = commerce or CommerceGateway()
    security = security or SecurityService()

    def prepare_metering(ticket):
        if isinstance(llm, LLMService):
            from app.services.metering import persist_usage
            llm.usage_recorder = lambda metadata: persist_usage(session.bind, ticket["id"], metadata)

    def bounded(value: object, limit: int | None = None) -> str:
        text = str(value or "")
        return text[: (limit or settings.agent_max_message_chars)]

    def triage_agent(state: SupportState) -> dict:
        started = perf_counter()
        ticket = state["ticket"]
        prepare_metering(ticket)
        conversation = redact_conversation(
            state.get("conversation", [])[-settings.agent_max_conversation_messages :]
        )
        conversation = [
            {**item, "content": bounded(item.get("content"))}
            for item in conversation
        ]
        security_assessment = security.assess(
            subject=ticket["subject"],
            message=ticket["message"],
            conversation=conversation,
        )
        ticket = {
            **ticket,
            "customer_name": redact_text(ticket["customer_name"]),
            "subject": bounded(redact_text(ticket["subject"])),
            "message": bounded(redact_text(ticket["message"])),
        }
        conversation_text = "\n".join(
            f"{('客户' if item.get('role') == 'customer' else '客服')}：{item.get('content', '')}"
            for item in conversation
        )
        result = llm.triage(ticket["subject"], conversation_text or ticket["message"])
        model_metadata = llm.call_metadata["triage"]
        triage = result.model_dump()
        ticket = {**ticket, "category": result.category, "priority": result.priority}
        failures = []
        if model_metadata.get("fallback"):
            failures.append(
                {
                    "code": "MODEL_TRIAGE_FALLBACK",
                    "stage": "triage",
                    "reason": model_metadata.get("failure_reason"),
                    "retry_count": model_metadata.get("retry_count", 0),
                }
            )
        return {
            "ticket": ticket,
            "triage": triage,
            "security": security_assessment.model_dump(),
            "model_trace": [{"stage": "triage", **model_metadata}],
            "failures": failures,
            "audit": [
                _event(
                    "分诊 Agent",
                    "意图识别与优先级判断",
                    f"识别为{result.category}，情绪{result.sentiment}，置信度{result.confidence:.0%}",
                    started,
                    category=result.category,
                    sentiment=result.sentiment,
                    security=security_assessment.model_dump(),
                    **model_metadata,
                )
            ],
        }

    def knowledge_agent(state: SupportState) -> dict:
        started = perf_counter()
        ticket = state["ticket"]
        recent_messages = " ".join(
            bounded(item.get("content", ""))
            for item in state.get("conversation", [])[-settings.agent_max_conversation_messages :]
            if item.get("role") == "customer"
        )
        query = build_retrieval_query(ticket, state.get("conversation", [])[-settings.agent_max_conversation_messages:])
        if isinstance(retrieval, RetrievalService):
            retrieval.workspace_id = ticket.get("workspace_id", "default")
        query = query[: settings.model_max_context_chars]
        citations, rag_status = retrieval.search_reliable(
            query, limit=settings.rag_max_documents
        )
        failures = []
        if not rag_status["reliable"]:
            failures.append(
                {
                    "code": "RAG_NO_RELIABLE_RESULT",
                    "stage": "knowledge",
                    "reason": rag_status["failure_reason"],
                    "retry_count": 0,
                }
            )
        return {
            "citations": [item.model_dump() for item in citations],
            "rag_status": rag_status,
            "failures": failures,
            "audit": [
                _event(
                    "知识检索 Agent",
                    "RAG 向量检索",
                    (
                        f"从知识库召回 {len(citations)} 条可靠政策依据"
                        if citations
                        else "没有达到可靠性阈值的知识依据"
                    ),
                    started,
                    event_status="completed" if citations else "degraded",
                    sources=[item.title for item in citations],
                    **rag_status,
                )
            ],
        }

    async def customer_agent(state: SupportState) -> dict:
        started = perf_counter()
        ticket = state["ticket"]
        from app.services.shop_assistant import shop_context
        context = None
        if isinstance(commerce, CommerceGateway):
            # Retrieval runs on another graph branch; never share its Session concurrently.
            with Session(bind=session.bind) as read_session:
                context = shop_context(read_session, ticket, state.get("conversation", []))
        if context is None:
            context = await commerce.aget_customer_context(ticket.get("order_id"), ticket["customer_name"])
        failures = []
        if context.get("tool_fallback"):
            failures.append(
                {
                    "code": "MCP_TOOL_FALLBACK",
                    "stage": "customer_context",
                    "reason": context["tool_fallback"],
                    "retry_count": context.get("tool_retry_count", 0),
                }
            )
        return {
            "customer_context": context,
            "failures": failures,
            "audit": [
                _event(
                    "客户上下文 Agent",
                    "查询商城与订单" if context.get("tool_source") == "shop_database" else "调用 MCP 业务工具",
                    ("已识别优惠咨询；活动规则由知识库提供，实时领券与发券记录未接入"
                     if context.get("assistant_route") == "promotions" else
                     f"已查询在售商品，匹配 {len(context.get('catalog', []))} 款"
                     if context.get("assistant_route") == "catalog" else
                     f"已查询订单 {context['order_id']} 与客户等级"
                     if context.get("context_available") is not False else "未提供订单号，无订单数据可核验"),
                    started,
                    event_status=(
                        "degraded" if context.get("tool_fallback") else "completed"
                    ),
                    tool_source=context.get("tool_source"),
                    risk_flags=context.get("risk_flags", []),
                    attempts=context.get("tool_attempts", 1),
                    retry_count=context.get("tool_retry_count", 0),
                    errors=context.get("tool_errors", []),
                )
            ],
        }

    def policy_agent(state: SupportState) -> dict:
        started = perf_counter()
        context = state["customer_context"]
        category = state["ticket"].get("category")
        ticket_amount = state["ticket"].get("amount")
        amount = float(ticket_amount if ticket_amount is not None else (context.get("order_amount") or 0))
        risk_flags = context.get("risk_flags", [])
        financial_action = requests_money_operation(state["ticket"])
        if state["ticket"].get("completed_operations") and not requests_money_operation(state["ticket"]):
            financial_action = False
        security_assessment = state.get("security", {})
        from app.services.shop_assistant import handoff_reasons
        reasons = handoff_reasons(state["ticket"], state.get("conversation", []), context)
        policy = {
            "max_refund": amount,
            "auto_approval_limit": 100.0,
            "risk_flags": risk_flags,
            "eligible": context.get("paid", False),
            "must_review": (
                bool(reasons)
                or bool(state["ticket"].get("handoff_active"))
                or (financial_action and (amount > 100 or bool(risk_flags)))
                or bool(security_assessment.get("requires_human_review"))
            ),
            "security_reasons": security_assessment.get("reasons", []),
            "financial_action_requested": requests_money_operation(state["ticket"]),
            "context_available": context.get("context_available", True),
            "handoff_reasons": reasons,
        }
        summary = "命中人工审批规则" if policy["must_review"] else "符合自动处理规则"
        return {
            "policy": policy,
            "audit": [
                _event(
                    "政策判断 Agent",
                    "合规与风险校验",
                    summary,
                    started,
                    amount=amount,
                    approval_limit=100,
                )
            ],
        }

    def resolution_agent(state: SupportState) -> dict:
        started = perf_counter()
        attempt = int(state.get("generation_attempt", 0)) + 1
        failures = []
        response_citations = state.get("citations", [])
        security_assessment = state.get("security", {})
        if security_assessment.get("blocked"):
            resolution = {
                "recommended_action": "转人工安全复核",
                "rationale": "请求涉及越权、指令注入或敏感账号操作，自动流程不得执行。",
                "draft_response": (
                    f"您好，{state['ticket']['customer_name']}。您的请求涉及需要额外安全核验的操作，"
                    "为保护您的账号和资金安全，系统不会自动执行，已转交人工客服复核。"
                ),
                "refund_amount": None,
                "requires_approval": True,
                "confidence": 1.0,
                "security_blocked": True,
            }
            model_metadata = {
                "provider": "security_guard",
                "model": "deterministic-policy",
                "fallback": False,
                "skipped": True,
                "attempts": 0,
                "retry_count": 0,
                "failure_reason": None,
                "errors": [],
                "security_reasons": security_assessment.get("reasons", []),
            }
        elif state["customer_context"].get("assistant_route") == "service_scope":
            resolution = service_scope_reply(state["ticket"], state["policy"]).model_dump()
            model_metadata = {"provider": "service_scope_guard", "model": "deterministic-policy", "skipped": True,
                              "fallback": False, "attempts": 0, "retry_count": 0, "errors": []}
        elif state["customer_context"].get("assistant_route") == "promotions":
            with Session(bind=session.bind) as read_session:
                faq = promotion_faq_citation(read_session, state["ticket"].get("workspace_id", "default"),
                                             response_citations, state.get("rag_status", {}))
            resolution = promotion_information_reply(state["policy"], faq).model_dump()
            response_citations = [faq] if faq else []
            model_metadata = {"provider": "promotion_faq_guard" if faq else "promotion_information_guard",
                              "model": "deterministic-policy", "skipped": True, "fallback": False,
                              "attempts": 0, "retry_count": 0, "errors": [],
                              "knowledge_source_id": faq["id"] if faq else None,
                              "knowledge_source": faq["source"] if faq else None}
        elif (state["customer_context"].get("assistant_route") == "catalog"
              and state["customer_context"].get("catalog_intent") == "availability_plan"
              and not state["customer_context"].get("availability_plan_available")):
            resolution = unavailable_catalog_plan_reply(state["customer_context"], state["policy"]).model_dump()
            model_metadata = {"provider": "catalog_information_guard", "model": "deterministic-policy", "skipped": True,
                              "fallback": False, "attempts": 0, "retry_count": 0, "errors": []}
        elif (state["customer_context"].get("assistant_route") == "catalog"
              and state["customer_context"].get("catalog_intent") == "details"):
            resolution = catalog_details_reply(state["customer_context"], state["policy"]).model_dump()
            model_metadata = {"provider": "catalog_details_guard", "model": "deterministic-policy", "skipped": True,
                              "fallback": False, "attempts": 0, "retry_count": 0, "errors": []}
        elif state["customer_context"].get("assistant_route") in {"catalog", "logistics"}:
            ticket_context = {**state["ticket"], "conversation": state.get("conversation", []),
                              "quality_feedback": state.get("quality_feedback")}
            result = llm.resolve(ticket_context, state["customer_context"], state.get("citations", []), state["policy"])
            model_metadata = llm.call_metadata["resolution"]
            resolution = result.model_dump()
            resolution["requires_approval"] = bool(state["policy"].get("must_review"))
            if model_metadata.get("fallback"):
                failures.append({"code": "MODEL_RESOLUTION_FALLBACK", "stage": "resolution",
                                 "reason": model_metadata.get("failure_reason"), "retry_count": model_metadata.get("retry_count", 0)})
        elif state["policy"].get("handoff_reasons"):
            resolution = {"recommended_action": "转人工客服处理", "rationale": "；".join(state["policy"]["handoff_reasons"]),
                          "draft_response": "您好，您的请求已记录，需要人工客服继续处理。本轮未执行退款或其他资金操作，您可以在此对话补充信息。",
                          "requires_approval": True, "confidence": 1.0, "refund_amount": (
                              (state["ticket"].get("amount") or state["customer_context"].get("order_amount"))
                              if state["policy"].get("financial_action_requested") and state["customer_context"].get("paid")
                              and 0 < (state["ticket"].get("amount") or state["customer_context"].get("order_amount") or 0) <= (state["customer_context"].get("order_amount") or 0) else None),
                          "safe_refusal": True}
            model_metadata = {"provider": "handoff_policy", "model": "not_called", "skipped": True,
                              "fallback": False, "attempts": 0, "retry_count": 0, "errors": []}
        elif (state["customer_context"].get("context_available") is False
              and needs_order_context(state["ticket"])):
            resolution = {
                "recommended_action": "补充订单信息后继续核验",
                "rationale": "缺少订单编号，不能声称已核实订单、付款状态或金额。",
                "draft_response": (
                    "您好，我们已收到您反馈的问题。为了准确核查，请您提供订单编号；"
                    "如暂时找不到，可在订单列表查看后回复此对话。请勿发送密码、验证码或完整银行卡号。"
                    "收到必要信息后，我们会继续核实并为您提供处理建议；当前尚未执行退款或补发。"
                ),
                "refund_amount": None,
                "requires_approval": bool(state["policy"].get("must_review")),
                "confidence": 0.0, "needs_clarification": True,
            }
            model_metadata = {
                "provider": "information_guard", "model": "not_called", "skipped": True,
                "fallback": False, "attempts": 0, "retry_count": 0, "errors": [],
                "failure_reason": "缺少订单号，先追问，不生成未经核验的订单结论",
            }
        elif not state.get("rag_status", {}).get("reliable", False):
            resolution = {
                "recommended_action": "转人工客服补充核验",
                "rationale": "知识库没有达到可靠性阈值的依据，系统拒绝生成无依据结论。",
                "draft_response": (
                    f"您好，{state['ticket']['customer_name']}。当前知识库中没有找到足够可靠的"
                    "处理依据，为避免向您提供不准确的信息，我们已将问题转交人工客服进一步核验。"
                ),
                "refund_amount": None,
                "requires_approval": True,
                "confidence": 0.0,
                "safe_refusal": True,
            }
            model_metadata = {
                "provider": "policy_guard",
                "model": "not_called",
                "fallback": False,
                "skipped": True,
                "attempts": 0,
                "retry_count": 0,
                "failure_reason": "RAG 无可靠结果，跳过方案模型",
                "errors": [],
            }
        else:
            ticket_context = {
                **state["ticket"],
                "conversation": state.get("conversation", []),
                "quality_feedback": state.get("quality_feedback"),
                "generation_attempt": attempt,
            }
            result = llm.resolve(
                ticket_context,
                state["customer_context"],
                state["citations"],
                state["policy"],
            )
            model_metadata = llm.call_metadata["resolution"]
            resolution = result.model_dump()
            # Business policy always wins over a probabilistic model decision.
            resolution["requires_approval"] = bool(
                state["policy"].get("must_review") or (
                    result.requires_approval and requests_money_operation(state["ticket"])
                )
            )
            resolution["raw_requires_approval"] = result.requires_approval
            if model_metadata.get("fallback"):
                failures.append(
                    {
                        "code": "MODEL_RESOLUTION_FALLBACK",
                        "stage": "resolution",
                        "reason": model_metadata.get("failure_reason"),
                        "retry_count": model_metadata.get("retry_count", 0),
                    }
                )
        return {
            "resolution": resolution,
            "citations": response_citations,
            "generation_attempt": attempt,
            "model_trace": [{"stage": "resolution", **model_metadata}],
            "failures": failures,
            "audit": [
                _event(
                    "方案生成 Agent",
                    "生成处置方案与回复",
                    resolution["recommended_action"],
                    started,
                    event_status=(
                        "degraded" if model_metadata.get("fallback") else "completed"
                    ),
                    confidence=resolution.get("confidence", 0),
                    generation_attempt=attempt,
                    **model_metadata,
                )
            ],
        }

    def quality_agent(state: SupportState) -> dict:
        started = perf_counter()
        resolution = state["resolution"]
        citations = state.get("citations", [])
        citations_valid = bool(citations) and all(
            item.get("id")
            and item.get("title")
            and item.get("source")
            and item.get("snippet")
            for item in citations
        )
        checks = {
            "has_policy_citation": citations_valid
            or state.get("customer_context", {}).get("assistant_route") in {"catalog", "logistics", "service_scope", "promotions"}
            or bool(resolution.get("safe_refusal") or resolution.get("needs_clarification")
                    or resolution.get("security_blocked")),
            "has_clear_action": bool(resolution.get("recommended_action")),
            "has_customer_response": len(resolution.get("draft_response", "")) >= 20,
            "approval_rule_respected": (
                not state["policy"].get("must_review") or resolution.get("requires_approval")
            ),
            "security_rule_respected": (
                not state.get("security", {}).get("blocked")
                or bool(resolution.get("security_blocked"))
            ),
        }
        checks.update(check_draft_facts(state["ticket"], state["customer_context"], state["policy"], resolution, citations))
        passed = all(checks.values())
        failed_checks = [name for name, passed_check in checks.items() if not passed_check]
        attempt = int(state.get("generation_attempt", 1))
        exhausted = (
            not passed
            and attempt > settings.quality_max_regenerations
        )
        failures = []
        if not passed:
            failures.append(
                {
                    "code": "QUALITY_CHECK_FAILED",
                    "stage": "quality",
                    "reason": f"未通过检查：{', '.join(failed_checks)}",
                    "retry_count": max(0, attempt - 1),
                    "exhausted": exhausted,
                }
            )
        if exhausted:
            from app.services.reply_guard import safe_review_reply
            resolution = {
                **resolution,
                "recommended_action": f"转人工复核：{resolution.get('recommended_action', '')}",
                "rationale": "回复经过有限次数重生成后仍未通过质量检查，必须转人工复核。",
                "requires_approval": True,
                "draft_response": safe_review_reply(),
                "refund_amount": None,
            }
        return {
            "resolution": resolution,
            "quality": {
                "passed": passed,
                "checks": checks,
                "failed_checks": failed_checks,
                "generation_attempt": attempt,
                "regeneration_count": max(0, attempt - 1),
                "retry_scheduled": not passed and not exhausted,
                "escalated": exhausted,
            },
            "quality_feedback": {"failed_checks": failed_checks},
            "failures": failures,
            "audit": [
                _event(
                    "质检 Agent",
                    "事实性与合规检查",
                    (
                        "全部检查通过"
                        if passed
                        else (
                            "重生成后仍未通过，已转人工"
                            if exhausted
                            else "质量检查未通过，准备重新生成"
                        )
                    ),
                    started,
                    event_status="completed" if passed else "degraded",
                    generation_attempt=attempt,
                    exhausted=exhausted,
                    **checks,
                )
            ],
        }

    def approval_gate(state: SupportState) -> dict:
        started = perf_counter()
        failures = [f for f in state.get("failures", []) if not (
            f.get("code") == "RAG_NO_RELIABLE_RESULT" and state.get("customer_context", {}).get("assistant_route") in {"catalog", "logistics", "service_scope", "promotions"})]
        failure_count = len(failures)
        pending = bool(
            state["resolution"].get("requires_approval")
            or failure_count >= settings.failure_escalation_threshold
        )
        if pending:
            decision = interrupt(
                {
                    "type": "human_approval_required",
                    "ticket_id": state["ticket"]["id"],
                    "ticket_no": state["ticket"]["ticket_no"],
                    "action": state["resolution"].get("recommended_action"),
                    "amount": state["resolution"].get("refund_amount"),
                    "failure_count": failure_count,
                    "failure_reasons": failures,
                }
            )
            approved = isinstance(decision, dict) and decision.get("decision") == "approve"
            gate_state = "approved" if approved else "rejected"
            summary = "人工已批准高风险动作" if approved else "人工已驳回高风险动作"
        else:
            gate_state = "auto_approved"
            summary = "低风险动作自动放行"
        return {
            "gate": {
                "state": gate_state,
                "failure_count": failure_count,
                "failure_reasons": failures,
            },
            "audit": [
                _event(
                    "审批门控 Agent",
                    "高风险动作门控",
                    summary,
                    started,
                    failure_count=failure_count,
                )
            ],
        }

    def route_after_quality(state: SupportState) -> str:
        quality = state.get("quality", {})
        if quality.get("passed") or quality.get("escalated"):
            return "approval_gate"
        return "resolution_agent"

    builder = StateGraph(SupportState)
    builder.add_node("triage_agent", traced_agent_node("triage_agent", triage_agent))
    builder.add_node("knowledge_agent", traced_agent_node("knowledge_agent", knowledge_agent))
    builder.add_node("customer_agent", traced_agent_node("customer_agent", customer_agent))
    builder.add_node("policy_agent", traced_agent_node("policy_agent", policy_agent))
    builder.add_node(
        "resolution_agent", traced_agent_node("resolution_agent", resolution_agent)
    )
    builder.add_node("quality_agent", traced_agent_node("quality_agent", quality_agent))
    builder.add_node("approval_gate", traced_agent_node("approval_gate", approval_gate))

    builder.add_edge(START, "triage_agent")
    # Fan-out: retrieval and business context are independent and may run in parallel.
    builder.add_edge("triage_agent", "knowledge_agent")
    builder.add_edge("triage_agent", "customer_agent")
    # Fan-in: policy evaluation waits until both upstream agents have completed.
    builder.add_edge(["knowledge_agent", "customer_agent"], "policy_agent")
    builder.add_edge("policy_agent", "resolution_agent")
    builder.add_edge("resolution_agent", "quality_agent")
    builder.add_conditional_edges(
        "quality_agent",
        route_after_quality,
        {
            "resolution_agent": "resolution_agent",
            "approval_gate": "approval_gate",
        },
    )
    builder.add_edge("approval_gate", END)
    return builder.compile(checkpointer=checkpointer)


def graph_topology() -> list[dict[str, str]]:
    return [
        {"from": "START", "to": "分诊 Agent"},
        {"from": "分诊 Agent", "to": "知识检索 Agent"},
        {"from": "分诊 Agent", "to": "客户上下文 Agent"},
        {"from": "知识检索 Agent", "to": "政策判断 Agent"},
        {"from": "客户上下文 Agent", "to": "政策判断 Agent"},
        {"from": "政策判断 Agent", "to": "方案生成 Agent"},
        {"from": "方案生成 Agent", "to": "质检 Agent"},
        {"from": "质检 Agent", "to": "方案生成 Agent（最多重试一次）"},
        {"from": "质检 Agent", "to": "审批门控 Agent"},
    ]
