"""Separate shopping conversations from actual after-sales issues without losing history."""
import re


def conversation_case_type(ticket):
    assessment = ticket.assessment or {}
    customer_texts = [str(m.get("content", "")) for m in assessment.get("conversation", []) if m.get("role") == "customer"]
    if not customer_texts:
        # Legacy subjects may be form/category labels, not the customer's actual need.
        customer_texts = [ticket.message or ticket.subject]
    for text in customer_texts:
        policy_question = bool(re.search(r"政策|规则|条件|是否支持|支持.{0,8}退|可以.{0,8}退|能不能.{0,8}退", text))
        # Actual faults remain after-sales even if the customer also asks about policy.
        incident = re.search(r"坏了|坏的|损坏|破损|质量.{0,6}问题|故障|缺件|漏发|错发|少发|尺寸不合适|投诉|重复扣款|多扣|付款.{0,6}(?:没|未|没有).{0,4}(?:订单|下单)|支付.{0,6}(?:异常|失败)|快递.{0,8}(?:不更新|没有更新|丢失)|物流.{0,8}(?:超时|超期|延误)|(?:没|未)收到货|一直.{0,6}(?:没收到|没到)|签收.{0,6}(?:争议|异常)", text)
        hypothetical = bool(re.search(r"如果|假如|假设|要是", text))
        explicit_request = bool(re.search(r"(?:我要|我想|帮我|直接|办理|要求|请为我|给我).{0,8}(?:退款|退货|退钱|换货|维修|赔付)", text))
        application = bool(re.search(r"申请.{0,6}(?:退款|退货|退钱|换货|维修|赔付)", text))
        if (incident and not hypothetical) or (explicit_request and not hypothetical) or (application and not policy_question and not hypothetical):
            return "after_sales"
        if not policy_question and not hypothetical and re.search(r"售后|退款.{0,10}(?:没到账|没有到账|未到账|进度)|我的.{0,6}退款|退的钱.{0,6}(?:没|未)到账", text):
            return "after_sales"
    if assessment.get("customer_context", {}).get("overdue"):
        return "after_sales"
    return "consultation"
