"""Customer-facing language, separate from citations and internal audit explanations."""

import re


CUSTOMER_REPLY_INSTRUCTION = (
    "客户话术：draft_response直接面向顾客，以本店客服身份说明答案和下一步。"
    "不要使用‘商家给的说明’‘商家知识库中写到’‘根据检索文档’等第三方或内部视角，"
    "不要向顾客展示知识库、RAG、检索阈值、质检或生成过程；来源和判断依据保留在citations/rationale等后台字段。"
    "已确认的优惠直接说明领取条件、金额和限制，不能删减条件或自行添加优惠。"
    "信息不足时自然说明具体哪项暂时无法确认，必要时追问，不说‘资料中未写明，我不会自行猜测’。"
    "普通咨询不要附加‘本轮未执行退款/发券’等无关操作清单，也不承诺之后必定查到、通知或执行。"
    "不因调整措辞隐瞒交易性质：成功的非真实资金操作仍须明确不处理真实资金，不代表真实到账。"
)

_INTERNAL_WORDING = re.compile(
    r"知识库|检索(?:文档|结果|阈值)|可靠性阈值|\bRAG\b|"
    r"(?:商家|商户)(?:给|提供|公布|发布)的?(?:优惠|促销|商品|活动)?(?:说明|资料|信息|规则)|"
    r"(?:根据|依据|以上为?)(?:商家|商户)(?:的)?(?:说明|资料)|"
    r"资料中未写明|不会自行猜测|"
    r"(?:本轮|当前)(?:尚未|未|没有)执行",
    re.IGNORECASE,
)


def uses_customer_language(reply: str) -> bool:
    """Check generated prose only; provenance remains available in backend fields."""
    return _INTERNAL_WORDING.search(reply) is None


def direct_faq_reply(answer: str) -> str:
    """Keep the canonical answer (including conditions) without exposing its storage."""
    answer = answer.strip()
    return answer if re.match(r"^(?:您好|你好|亲)[，,！!。\s]", answer) else f"您好，{answer}"


def human_handoff_reply(reasons=()) -> str:
    """Explain the customer-relevant reason, never dump internal policy flags."""
    reason_text = "；".join(reasons)
    if "退款或资金" in reason_text:
        explanation = "退款等资金操作需要由人工客服核验后处理"
    elif "重复扣款" in reason_text:
        explanation = "这笔扣款需要由人工客服进一步核对"
    elif "运输天数" in reason_text:
        explanation = "包裹运输已超过预计时效，需要人工客服进一步协助"
    elif "发券" in reason_text:
        explanation = "发放优惠券需要由人工客服核验后处理"
    else:
        explanation = "这个问题需要人工客服进一步协助"
    return f"您好，{explanation}，已为您转接。您可以继续在这里补充情况，无需重复提交。"

