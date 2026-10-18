"""Deterministic fallback triage using customer intent, never assistant suggestions."""

import re


def customer_messages(message: str) -> list[str]:
    """Accept the graph's labelled transcript as well as a plain customer message."""
    if not re.search(r"(?:^|\n)客户：", message):
        return [message]
    return [part.split("\n客服：", 1)[0].strip()
            for part in re.split(r"(?:^|\n)客户：", message)[1:]]


def _category(text: str) -> str | None:
    if re.search(r"退款|退货|退钱|refund|退给|返还.{0,6}扣款", text):
        return "refund"
    if re.search(r"支付|扣款|付款|发票|冻结款|银行卡|积分.*(?:返回|退回)", text):
        return "payment"
    if re.search(r"缺件|配件|少发|漏发|错发|数量不足|只有[一二三四五六七八九十两\d]+件|补发", text) and not re.search(
            r"丢件|丢失|包装破损|包裹破损|快递破损", text):
        return "product"
    if re.search(r"物流|快递|包裹|配送|签收|代收点|收货地址|出库|运输|没收到|延迟", text):
        return "logistics"
    if re.search(r"账号|账户|登录|密码|会员|验证码|绑定.*手机|手机号", text):
        return "account"
    if re.search(r"商品|损坏|质量|尺寸|颜色|规格|配件|充电器|不能使用|耳机|键盘|鼠标|水杯", text):
        return "product"
    return None


def classify_support_intent(subject: str, message: str) -> str:
    customers = customer_messages(message)
    latest = customers[-1].lower()
    # An explicit new question wins over stale history/title. Greetings and
    # out-of-scope questions must not inherit an old refund/payment topic.
    if re.search(r"python|编程|作文|旅游|学习计划|生活规划|优惠券|满减|领券", latest):
        return _category(latest) or "other"
    if re.fullmatch(r"\s*(?:您好|你好|hello|hi|在吗)[！!。？?\s]*", latest):
        return "other"
    category = _category(latest)
    evidence_followup = bool(re.search(r"照片|凭证|材料|清单", latest) and
                             re.search(r"补充|上传|已|显示|继续", latest))
    procedure_followup = bool(re.search(r"怎么|如何|哪里|需要.*什么", latest) and re.search(r"申请|办理", latest))
    if category is None or (category == "product" and (evidence_followup or procedure_followup)):
        for previous in reversed(customers[:-1]):
            inherited = _category(previous.lower())
            if inherited:
                return inherited
        # The title is customer-provided context too, but only a fallback, not
        # a reason to override a genuine topic change in the latest question.
        return _category(subject.lower()) or category or "other"
    return category
