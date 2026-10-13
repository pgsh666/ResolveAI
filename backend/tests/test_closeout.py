import asyncio
import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.core.config import Settings
from app.core.database import SessionLocal
from app.main import app
from app.models import Approval, KnowledgeDocument, KnowledgeEmbedding, Ticket
from app.schemas import ResolutionResult
from app.services.llm import LLMService
from app.services.commerce import CommerceGateway
from app.services.embeddings import EmbeddingService
from app.services.knowledge_index import ensure_semantic_index
from app.services.reply_validation import check_draft_facts, extract_order_identifier, requests_money_operation
from app.services.retrieval_query import build_retrieval_query
from app.services.tool_execution import ToolBusinessRuleError, ToolExecutionService
from app.core import observability
from app.tasks import jobs
from evaluation.run import load_cases, validate_dataset


def test_missing_order_never_fabricates_context():
    context = asyncio.run(CommerceGateway(Settings(_env_file=None, agent_mode="demo")).aget_customer_context(None, "测试客户"))
    assert context["order_id"] is None
    assert context["order_amount"] is None
    assert context["paid"] is False
    assert context["tool_source"] == "not_called"


def test_chinese_followup_extracts_order_and_negative_request_is_not_refund():
    assert extract_order_identifier("订单号是ORD-CLOSE-222，我申请退款180元。") == "ORD-CLOSE-222"
    assert not requests_money_operation({"subject": "退款与售后", "message": "请说明退款到账时间，我只是查询进度，不申请新的退款。"})


def test_completed_simulated_refund_requires_disclosure_and_does_not_authorize_other_actions():
    ticket = {"order_id": "ORD-222", "completed_operations": [{"tool_name": "execute_refund", "result": {
        "success": True, "order_id": "ORD-222", "mode": "simulated",
    }}]}
    assert check_draft_facts(ticket, {}, {}, {"draft_response": "我们已为您退款；本次为项目模拟执行。"})["no_unexecuted_action_claim"]
    assert not check_draft_facts(ticket, {}, {}, {"draft_response": "我们已为您退款。"})["no_unexecuted_action_claim"]
    assert not check_draft_facts(ticket, {}, {}, {"draft_response": "本次为模拟退款，我们已为您发起调查。"})["no_unexecuted_action_claim"]


@pytest.mark.parametrize("subject,message,expected", [
    ("退款进度", "退款还没有到账，请帮我查一下。", False),
    ("商品质量问题", "我希望直接退款88元。", True),
    ("冻结款", "银行还未释放冻结款。", False),
    ("退货申请", "我想申请退货退款。", True),
])
def test_financial_operation_is_distinct_from_consultation(subject, message, expected):
    assert requests_money_operation({"subject": subject, "message": message}) is expected


def test_receiving_refund_inquiry_is_not_an_execution_claim():
    checks = check_draft_facts(
        {}, {}, {}, {"draft_response": "我们已收到您的退款咨询，当前尚未执行退款。"},
    )
    assert checks["no_unexecuted_action_claim"]
    checks = check_draft_facts({}, {}, {}, {"draft_response": "我们已为您发起调查，并补发商品。"})
    assert not checks["no_unexecuted_action_claim"]


def test_unknown_order_and_unsolicited_refund_are_rejected():
    checks = check_draft_facts(
        {"subject": "商品颜色不对", "message": "希望了解需要哪些凭证。"},
        {"context_available": False}, {"max_refund": 88},
        {"draft_response": "已为您找到关联订单ORD-10024。", "refund_amount": 88},
    )
    assert not checks["order_reference_supported"]
    assert not checks["missing_order_not_claimed_verified"]
    assert not checks["refund_amount_supported"]


def test_missing_order_workflow_asks_for_information():
    with TestClient(app) as client:
        ticket = client.post("/api/v1/tickets", json={
            "customer_name": "缺单验收", "subject": "付款失败", "message": "支付失败，我找不到订单号。",
        }).json()
        detail = client.post(f"/api/v1/tickets/{ticket['id']}/analyze").json()
        assert detail["status"] == "in_progress"
        assert "请您提供订单编号" in detail["draft_response"]
        assert "ORD-10024" not in detail["draft_response"]
        assert detail["assessment"]["customer_context"]["context_available"] is False


def test_approval_after_old_reply_keeps_new_round_draft():
    with TestClient(app) as client:
        ticket = client.post("/api/v1/customer/tickets", json={
            "customer_name": "多轮审批验收", "subject": "退款与售后", "message": "商品有质量问题，想申请退款。",
        }).json()
        ticket_id = ticket["id"]
        first = client.post(f"/api/v1/tickets/{ticket_id}/analyze").json()
        assert client.post(f"/api/v1/tickets/{ticket_id}/draft/send", json={"response": first["draft_response"]}).status_code == 200
        followup = client.post(f"/api/v1/customer/tickets/{ticket_id}/messages", json={"message": "订单号是ORD-CLOSE-222，我申请退款180元。"})
        assert followup.status_code == 200
        pending = client.post(f"/api/v1/tickets/{ticket_id}/analyze").json()
        assert pending["status"] == "pending_approval"
        assert "退款" in pending["approval"]["action"] and "180" in pending["approval"]["action"]
        assert pending["order_id"] == "ORD-CLOSE-222" and pending["amount"] == 180
        approved = client.post(f"/api/v1/tickets/{ticket_id}/decision", json={"decision": "approve", "reviewer": "验收审核员", "note": "批准"})
        assert approved.status_code == 200
        assert approved.json()["draft_response"]
        assert approved.json()["status"] == "in_progress"


@pytest.mark.parametrize("amount,authorized", [(180, True), (1000, False)])
def test_explicit_valid_request_fills_missing_approval_amount_not_model_guess(monkeypatch, amount, authorized):
    def resolve_without_amount(self, ticket, customer_context, citations, policy):
        self.call_metadata["resolution"] = {"provider": "demo", "fallback": False}
        return ResolutionResult(recommended_action="人工审批", rationale="需人工审核客户的退款申请。",
                                draft_response="您好，已收到您的退款申请，需人工审核，尚未执行。",
                                refund_amount=None, requires_approval=True, confidence=0.9)
    monkeypatch.setattr(LLMService, "resolve", resolve_without_amount)
    with TestClient(app) as client:
        ticket = client.post("/api/v1/customer/tickets", json={
            "customer_name": "金额范围验收", "subject": "退款与售后", "message": f"我申请退款{amount}元。",
            "order_id": "ORD-CLOSE-222", "amount": amount,
        }).json()
        response = client.post(f"/api/v1/tickets/{ticket['id']}/analyze")
        assert response.status_code == 200, response.text
        pending = response.json()
        assert pending["status"] == "pending_approval"
        assert pending["assessment"]["refund_amount"] is None
        assert pending["approval"]["amount"] == (amount if authorized else None)


def test_negative_amount_is_rejected_at_ticket_boundary():
    with TestClient(app) as client:
        response = client.post("/api/v1/tickets", json={
            "customer_name": "金额验收", "subject": "退款", "message": "申请退款", "amount": -1,
        })
        assert response.status_code == 422


def test_query_uses_customer_history_not_support_suggestions():
    query = build_retrieval_query({"category": "product", "subject": "数量不足"}, [
        {"role": "customer", "content": "三件商品只到了两件。"},
        {"role": "support", "content": "建议直接退款999元"},
    ])
    assert "商品缺件" in query
    assert "999" not in query


def test_versioned_index_preserves_legacy_vectors_and_updates_changed_content():
    class FakeSemantic:
        profile = "unit-semantic:512"
        provider = "fastembed"

        def embed_documents(self, texts):
            return [[1.0] + [0.0] * 511 for _ in texts]

    with TestClient(app):
        with SessionLocal() as session:
            document = session.scalar(select(KnowledgeDocument))
            original = list(document.embedding)
            index = ensure_semantic_index(session, FakeSemantic())
            assert index["updated"] >= 21
            again = ensure_semantic_index(session, FakeSemantic())
            assert again["updated"] == 0
            document.content += " 单元测试版本更新。"
            session.commit()
            updated = ensure_semantic_index(session, FakeSemantic())
            assert updated["updated"] == 1
            session.refresh(document)
            assert list(document.embedding) == original
            record = session.scalar(select(KnowledgeEmbedding).where(
                KnowledgeEmbedding.document_id == document.id,
                KnowledgeEmbedding.profile == FakeSemantic.profile,
            ))
            assert record.dimensions == 512
            # Restore corpus so this test does not influence other tests' retrieval results.
            document.content = document.content.removesuffix(" 单元测试版本更新。")
            session.commit()


def test_holdout_is_distinct_and_has_all_required_coverage():
    directory = Path(__file__).parents[1] / "evaluation"
    regression = load_cases()
    holdout = load_cases(directory / "holdout.jsonl")
    validate_dataset(holdout, holdout=True)
    assert len(holdout) == 25
    assert not {case.id for case in regression}.intersection(case.id for case in holdout)
    assert not {case.message for case in regression}.intersection(case.message for case in holdout)


def test_missing_semantic_weights_fail_closed(tmp_path):
    settings = Settings(_env_file=None, agent_mode="demo", embedding_provider="fastembed", embedding_cache_dir=str(tmp_path))
    with pytest.raises(RuntimeError, match="模型清单"):
        _ = EmbeddingService(settings).profile


def test_rq_job_flushes_even_on_failure_without_masking_error(monkeypatch):
    calls = []
    monkeypatch.setattr(jobs, "flush_tracing", lambda: calls.append("flush"))
    monkeypatch.setattr(jobs, "update_current_task", lambda *args, **kwargs: None)
    with TestClient(app):
        with pytest.raises(LookupError, match="工单不存在"):
            jobs.analyze_ticket_job("nonexistent-closeout-ticket")
    assert calls == ["flush"]


def test_trace_flush_failure_does_not_crash_business(monkeypatch):
    class FailedExporter:
        def force_flush(self, **kwargs):
            raise RuntimeError("exporter unavailable")
    monkeypatch.setattr(observability, "_provider", FailedExporter())
    assert observability.flush_tracing() is False


@pytest.mark.parametrize("context", [
    {"order_amount": 100, "paid": True},
    {"order_amount": 200, "paid": False},
    {"order_amount": 200, "paid": True, "tool_fallback": "upstream timeout"},
])
def test_refund_scope_rejects_overpaid_unpaid_and_fallback_context(context):
    ticket = Ticket(order_id="ORD-222", category="refund", assessment={"customer_context": context})
    approval = Approval(action="退款180元", amount=180)
    with pytest.raises(ToolBusinessRuleError):
        ToolExecutionService._validate_business_scope(ticket, approval, "execute_refund", {"order_id": "ORD-222", "amount": 180})
