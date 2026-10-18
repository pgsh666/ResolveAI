"""Shared commerce promotion intent and campaign aliases for routing and retrieval."""

import re


CAMPAIGNS = (
    ("双十一", re.compile(r"双\s*(?:十一|11(?!\d))|(?<!\d)11[.·]11(?!\d|\s*[元块件天])")),
    ("双十二", re.compile(r"双\s*(?:十二|12(?!\d))|(?<!\d)12[.·]12(?!\d|\s*[元块件天])")),
    ("618", re.compile(r"(?<![a-zA-Z\d])6[.·]?18(?!\d|\s*[元块件天])|六一八")),
    ("店庆", re.compile(r"店庆")), ("周年庆", re.compile(r"周年庆")),
    ("年货节", re.compile(r"年货节")), ("购物节", re.compile(r"购物节")),
    ("会员日", re.compile(r"会员日")), ("黑五", re.compile(r"黑五|黑色星期五")),
)
PROMOTION_WORDS = re.compile(
    r"优惠券|折扣券|代金券|满减券|领券|用券|有(?:什么|哪些|可用|能用的)?券|券码|优惠码|优惠|折扣|打折|满减|促销|活动价|特价|"
    r"(?:商城|店铺|店里|你们|你家|咱们).{0,6}活动|"
    r"^(?:现在|今天|最近)?有(?:什么|哪些|啥)?活动[吗么嘛？?\s]*$|^活动(?:有哪些|详情|规则|介绍|时间)[吗么嘛？?\s]*$")
FOLLOWUP = re.compile(
    r"怎么[领用]|哪里领|在哪[领用]|如何[领用]|使用条件|有效期|满多少|叠加|能领吗|"
    r"什么时候(?:开始|结束|公布)|何时(?:开始|结束|公布)|几号(?:开始|结束)|多久(?:开始|结束)|活动规则|还没公布|公布了吗")
AFTER_SALES = re.compile(r"退款|退货|退钱|退换|售后|损坏|破损|坏了|缺件|错发|投诉|维修")
OUT_OF_SCOPE = re.compile(r"Python|编程|作文|写作|旅游|学习计划|生活规划|(?:学校|校园|班级|幼儿园|社团).{0,10}(?:活动|策划|运动会)", re.I)
ORDER_QUERY = re.compile(r"物流|快递|配送|发货|签收|扣款|付款|支付|订单.{0,10}(?:到哪|状态|查询)")
CAMPAIGN_TITLE = re.compile(r"[\u4e00-\u9fff\d]{2,20}(?:活动|大促)[吗呢呀啊？?！!。\s]*")


def campaign_names(text: str) -> list[str]:
    return [name for name, pattern in CAMPAIGNS if pattern.search(text)]


def normalize_campaign_names(text: str) -> str:
    for name, pattern in CAMPAIGNS:
        text = pattern.sub(name, text)
    return text


def is_promotion_question(text: str) -> bool:
    if AFTER_SALES.search(text) or OUT_OF_SCOPE.search(text):
        return False
    if PROMOTION_WORDS.search(text):
        return True
    return bool(campaign_names(text) or CAMPAIGN_TITLE.fullmatch(text.strip())) and not ORDER_QUERY.search(text)


def promotion_context(latest: str, previous: list[str]) -> str | None:
    """Inherit only an uninterrupted customer-stated promotion topic."""
    direct = is_promotion_question(latest)
    if direct and (campaign_names(latest) or not FOLLOWUP.search(latest)):
        return normalize_campaign_names(latest)
    if AFTER_SALES.search(latest) or OUT_OF_SCOPE.search(latest) or ORDER_QUERY.search(latest) or not FOLLOWUP.search(latest):
        return normalize_campaign_names(latest) if direct else None
    for message in reversed(previous[-10:]):
        if is_promotion_question(message):
            return normalize_campaign_names(f"{message} {latest}")
        if not FOLLOWUP.search(message):
            break
    return normalize_campaign_names(latest) if direct else None
