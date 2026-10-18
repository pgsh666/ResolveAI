from types import SimpleNamespace

import pytest

from app.services.promotion_intent import campaign_names, is_promotion_question, promotion_context
from app.services.retrieval_query import build_retrieval_query
from app.services.shop_assistant import shop_context


@pytest.mark.parametrize("message,name", [("双11活动", "双十一"), ("双十一", "双十一"),
                                         ("双12大促", "双十二"), ("六一八活动", "618"), ("6.18活动", "618"),
                                         ("618活动", "618"), ("店庆活动", "店庆"), ("年货节", "年货节")])
def test_campaign_aliases_are_shared_by_routing_and_retrieval(message, name):
    assert campaign_names(message) == [name]
    assert is_promotion_question(message)
    ticket = {"channel": "web", "message": message, "subject": "旧的耳机咨询", "category": "other"}
    query = build_retrieval_query(ticket, [{"role": "customer", "content": "帮我找耳机"},
                                         {"role": "support", "content": "已经发放999元优惠券"},
                                         {"role": "customer", "content": message}])
    assert name in query and "耳机" not in query and "999" not in query


@pytest.mark.parametrize("message", ["双十一订单什么时候发货", "618买的快递到哪了", "双11付款失败",
                                     "耳机价格618元吗", "耳机价格11.11元吗", "水杯12.12元吗",
                                     "订单号618001物流状态", "帮我策划学校双十一活动",
                                     "帮我规划Python学习活动", "双十一买的商品坏了"])
def test_campaign_words_do_not_capture_other_business_or_noncommerce_tasks(message):
    assert not is_promotion_question(message)


def test_followup_inherits_only_uninterrupted_promotion_topic():
    assert "双十一" in promotion_context("什么时候开始", ["双11活动"])
    assert "双十一" in promotion_context("什么时候结束", ["双11活动", "什么时候开始"])
    assert "双十二" in promotion_context("活动规则", ["双12活动"])
    assert promotion_context("什么时候开始", ["双11活动", "帮我找耳机"]) is None
    assert promotion_context("什么时候开始", ["双11活动", "我要退款"]) is None
    assert promotion_context("什么时候开始", ["双11活动", "帮我学习Python"]) is None


def test_exact_campaign_title_does_not_depend_on_triage_category():
    session = SimpleNamespace(scalars=lambda *args, **kwargs: SimpleNamespace(all=lambda: []))
    for category in ["other", "product", "payment"]:
        context = shop_context(session, {"channel": "web", "customer_name": "测试客户", "message": "双十一活动",
                                         "category": category}, [{"role": "customer", "content": "双十一活动"}])
        assert context["assistant_route"] == "promotions"
