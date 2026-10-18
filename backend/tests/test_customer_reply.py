"""Customer copy must stay natural without weakening provenance or action safety."""

from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from app.core.config import Settings
from app.main import app
from app.schemas import DraftRewriteResult, ResolutionResult
from app.services.customer_reply import (
    CUSTOMER_REPLY_INSTRUCTION, direct_faq_reply, human_handoff_reply, uses_customer_language,
)
from app.services.llm import LLMService
from app.services.reply_guard import NON_REAL_FUNDS_NOTICE, safe_review_reply
from app.services.reply_validation import check_draft_facts
from app.services.shop_assistant import demo_shop_reply, promotion_information_reply, unavailable_catalog_plan_reply
from evaluation.run import _expected_outcome_matches, load_cases


@pytest.mark.parametrize("text", [
    "商家知识库《优惠》中的说明是：收藏领券。", "根据检索文档，收藏领券。",
    "以上为商家公布的优惠说明。", "商家给的说明是收藏领券。",
    "资料中未写明的信息无法确认，我不会自行猜测。", "本轮未执行领券或发券操作。",
    "当前尚未执行退款或补发。", "RAG 结果未达到可靠性阈值。",
])
def test_internal_process_wording_is_not_customer_language(text):
    assert not uses_customer_language(text)
    assert not check_draft_facts({}, {}, {}, {"draft_response": text})["customer_facing_language"]


@pytest.mark.parametrize("text", [
    "您好，收藏店铺可领3元优惠券，满50元可用。",
    "这款商品暂时没有更详细的介绍，您想了解哪个方面？",
    "具体补货时间暂时无法确认，也可以看看其他在售款式。",
    "请参考商品说明书中的使用步骤。", NON_REAL_FUNDS_NOTICE,
])
def test_customer_facts_limitations_and_funds_disclosures_remain_allowed(text):
    assert uses_customer_language(text)


@pytest.mark.parametrize("answer", ["收藏店铺领3元券。", "您好，收藏店铺可领3元券，满50元可用。"])
def test_direct_faq_keeps_exact_conditions_without_duplicate_greeting(answer):
    reply = direct_faq_reply(answer)
    assert answer in reply and reply.count("您好") == 1
    assert uses_customer_language(reply)


def test_short_verified_faq_is_sent_without_handoff_and_keeps_backend_source():
    from uuid import uuid4
    with TestClient(app) as client:
        created = client.post("/api/v1/knowledge/documents", json={
            "title": "能给我优惠券吗", "content": "收藏店铺即可领取3元优惠券。", "category": "general",
            "source": "faq://manual/" + uuid4().hex,
        })
        assert created.status_code == 201, created.text
        document = created.json()
        try:
            result = client.post("/api/v1/customer/tickets", json={"message": "有优惠券吗"})
            assert result.status_code == 201, result.text
            ticket = result.json()
            assert ticket["sent_response"] == "您好，收藏店铺即可领取3元优惠券。"
            assert len(ticket["sent_response"]) < 20
            assert not ticket["handed_off"]
            detail = client.get(f"/api/v1/tickets/{ticket['id']}").json()
            assert detail["citations"][0]["id"] == document["id"]
            assert detail["citations"][0]["source"] == document["source"]
            assert document["title"] in detail["assessment"]["rationale"]
            assert detail["assessment"]["quality"]["passed"]
        finally:
            client.patch(f"/api/v1/knowledge/documents/{document['id']}", json={"enabled": False})


def test_unknown_offer_restocks_and_catalog_comparison_use_customer_language():
    responses = [
        promotion_information_reply({}).draft_response,
        unavailable_catalog_plan_reply({"catalog_topic": "键盘"}, {}).draft_response,
        demo_shop_reply({}, {"assistant_route": "catalog", "catalog": [
            {"name": "入门键盘", "description": "有线键盘", "price": 99, "stock": 10},
            {"name": "机械键盘", "description": "机械轴键盘", "price": 199, "stock": 5},
        ]}, {}).draft_response,
        demo_shop_reply({}, {"assistant_route": "logistics", "order_id": "ORD-1",
                             "order_status": "in_transit", "order_status_label": "运输中",
                             "transit_days": 1, "expected_delivery_days": 3}, {}).draft_response,
    ]
    assert all(uses_customer_language(reply) for reply in responses)
    assert not any(word in "\n".join(responses) for word in ["模拟", "以上为", "没有记录"])
    assert "99.00" in responses[2] and "199.00" in responses[2]
    assert "已运输 1 天" in responses[3] and "预计运输时间为 3 天" in responses[3]


@pytest.mark.parametrize("message", ["请转人工客服", "我要退款", "银行卡重复扣款，请核查518元", "请给我发放10元优惠券"])
def test_customer_handoff_does_not_dump_internal_reasons_or_operation_list(message):
    with TestClient(app) as client:
        result = client.post("/api/v1/customer/tickets", json={"message": message})
        assert result.status_code == 201, result.text
        ticket = result.json()
        assert ticket["handed_off"] and ticket["handoff_reasons"]
        assert "转接" in ticket["sent_response"] and uses_customer_language(ticket["sent_response"])
        assert "原因：" not in ticket["sent_response"]
        if message == "请转人工客服":
            assert "资金操作" not in ticket["sent_response"]
        assert "已为您退款" not in ticket["sent_response"] and "已发放" not in ticket["sent_response"]


def test_safe_fallback_retains_non_real_money_notice_only_when_needed():
    ticket = {"order_id": "ORD-1", "completed_operations": [{"tool_name": "execute_refund",
              "result": {"success": True, "order_id": "ORD-1", "mode": "simulated"}}]}
    assert NON_REAL_FUNDS_NOTICE in safe_review_reply(ticket)
    assert NON_REAL_FUNDS_NOTICE not in safe_review_reply()
    assert uses_customer_language(safe_review_reply(ticket))
    assert uses_customer_language(human_handoff_reply(["RAG_NO_RELIABLE_RESULT"]))
    assert "RAG" not in human_handoff_reply(["RAG_NO_RELIABLE_RESULT"])


def test_generation_and_rewrite_share_style_guidance_without_real_api(monkeypatch):
    service = LLMService(Settings(_env_file=None, agent_mode="demo"))
    service.client = object()
    prompts = {}
    def fake_call(stage, schema, system_prompt, user_prompt):
        prompts[stage] = system_prompt
        if stage == "rewrite":
            return DraftRewriteResult(draft_response="您好，收藏店铺可领3元优惠券。")
        return ResolutionResult(recommended_action="说明领取条件", rationale="依据已启用优惠规则",
                                draft_response="您好，收藏店铺可领3元优惠券。", requires_approval=False, confidence=1)
    monkeypatch.setattr(service, "_call_with_retry", fake_call)
    service.resolve({"completed_operations": [{"tool_name": "execute_refund",
                     "result": {"success": True, "mode": "simulated"}}]}, {}, [], {})
    service.rewrite({}, "原回复", "更自然一些")
    for prompt in prompts.values():
        assert CUSTOMER_REPLY_INSTRUCTION in prompt
        assert NON_REAL_FUNDS_NOTICE in prompt


@pytest.mark.parametrize("always_invalid", [False, True])
def test_model_internal_wording_is_regenerated_or_safely_handed_off(monkeypatch, always_invalid):
    calls = []
    def fake_resolve(self, ticket, context, citations, policy):
        calls.append(ticket)
        self.call_metadata["resolution"] = {"provider": "test", "fallback": False, "attempts": 1}
        draft = ("您好，商家知识库中写到，可以根据商品说明书核对使用步骤。"
                 if len(calls) == 1 or always_invalid else
                 "您好，您可以先参考商品说明书核对使用步骤，也可以告诉我具体在哪一步遇到了问题。")
        return ResolutionResult(recommended_action="提供使用建议", rationale="依据商品使用规则",
                                draft_response=draft, requires_approval=False, confidence=1)
    monkeypatch.setattr(LLMService, "resolve", fake_resolve)
    with TestClient(app) as client:
        ticket = client.post("/api/v1/tickets", json={
            "customer_name": "话术测试用户", "subject": "商品使用说明",
            "message": "请根据商品说明提供使用建议。", "order_id": "ORD-TONE-01",
        }).json()
        response = client.post(f"/api/v1/tickets/{ticket['id']}/analyze")
        assert response.status_code == 200, response.text
        detail = response.json()
        assert len(calls) == 2
        assert "customer_facing_language" in calls[-1]["quality_feedback"]["failed_checks"]
        assert uses_customer_language(detail["draft_response"])
        assert detail["assessment"]["quality"]["escalated"] is always_invalid
        assert detail["requires_approval"] is always_invalid


def test_safe_refusal_evaluation_checks_state_not_a_fixed_customer_phrase():
    case = next(case for case in load_cases() if case.expected_outcome == "safe_refusal")
    ticket = SimpleNamespace(status="pending_approval", requires_approval=True,
                             draft_response="您好，已为您转接人工客服，请在这里补充情况。")
    assessment = {"resilience": {"rag": {"reliable": False}}, "resolution": {"safe_refusal": True}}
    assert _expected_outcome_matches(case, ticket, assessment)
    for bad_assessment in [{"resilience": {"rag": {"reliable": False}}},
                           {**assessment, "resilience": {"rag": {"reliable": True}}}]:
        assert not _expected_outcome_matches(case, ticket, bad_assessment)
    ticket.requires_approval = False
    assert not _expected_outcome_matches(case, ticket, assessment)

