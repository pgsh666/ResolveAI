import pytest

from app.services.reply_guard import NON_REAL_FUNDS_NOTICE, check_reply_evidence, has_funds_disclosure
from app.services.reply_validation import check_draft_facts


def completed_refund():
    return {"order_id": "ORD-1", "message": "请退款", "completed_operations": [{"tool_name": "execute_refund",
            "result": {"success": True, "order_id": "ORD-1", "mode": "simulated"}}]}


@pytest.mark.parametrize("notice", [NON_REAL_FUNDS_NOTICE, "本项目为模拟操作，不代表真实到账。", "演示环境，不代表真实到账。"])
def test_clear_non_real_money_notice_is_compatible_with_verified_refund(notice):
    draft = f"已为您退款。{notice}"
    assert has_funds_disclosure(draft)
    assert all(check_reply_evidence(completed_refund(), {}, [], draft).values())
    checks = check_draft_facts(completed_refund(), {}, {}, {"draft_response": draft}, [])
    assert checks["no_unexecuted_action_claim"]
    assert checks["simulated_result_disclosed"]


@pytest.mark.parametrize("draft", ["已为您退款。", "已为您退款，金额已到账。", "请查看退款进度。"])
def test_generic_text_is_not_a_non_real_funds_disclosure(draft):
    assert not has_funds_disclosure(draft)
    assert not check_reply_evidence(completed_refund(), {}, [], draft)["simulated_result_disclosed"]


def test_notice_never_authorizes_or_proves_an_unexecuted_refund():
    draft = "已为您退款。" + NON_REAL_FUNDS_NOTICE
    ticket = {"order_id": "ORD-1", "message": "请退款"}
    assert not check_reply_evidence(ticket, {}, [], draft)["action_state_has_evidence"]
    assert not check_draft_facts(ticket, {}, {}, {"draft_response": draft})["no_unexecuted_action_claim"]


def test_notice_does_not_accept_another_orders_refund_as_evidence():
    ticket = completed_refund()
    ticket["completed_operations"][0]["result"]["order_id"] = "ORD-OTHER"
    draft = "已为您退款。" + NON_REAL_FUNDS_NOTICE
    assert not check_draft_facts(ticket, {}, {}, {"draft_response": draft})["no_unexecuted_action_claim"]
