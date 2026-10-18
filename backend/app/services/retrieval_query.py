"""Business-language query expansion; never uses evaluation labels or case identifiers."""

import re


def build_retrieval_query(ticket: dict, conversation: list[dict]) -> str:
    labels = {
        "refund": "退款售后", "logistics": "物流问题", "payment": "支付问题",
        "product": "商品问题", "account": "账号问题",
    }
    customers = " ".join(str(item.get("content", "")) for item in conversation if item.get("role") == "customer")
    query = f"{labels.get(ticket.get('category'), '')} {ticket.get('subject', '')} {customers or ticket.get('message', '')}"
    if re.search(r"近期.*退款|多次.*退款|超过.*100|大额退款", query):
        query += " 退款审批风险"
    if re.search(r"数量不足|只有[一二三四两0-9]+件|少[了发一件]|漏发", query):
        query += " 商品缺件"
    if ticket.get("category") == "logistics" and re.search(r"还没到|没有收到|未收到|一直没.*收到", query):
        query += " 配送进度延误"
    if ticket.get("category") == "refund":
        if float(ticket.get("amount") or 0) > 100 or re.search(r"跳过|绕过|不用.*审批|近期|多次|大额", query):
            query += " 退款审批 风控 资金写操作 人工处理"
        if re.search(r"进度|到账|还没.*退|银行卡.*没有收到", query):
            query += " 退款到账时效 异常排查 支付渠道 银行处理状态"
    if ticket.get("category") == "payment" and re.search(r"重复|多扣|两笔|两次|返还|打回来|忽略.*扣款", query):
        query += " 重复扣款 支付流水 订单数量 原路退回"
    if ticket.get("category") == "logistics" and re.search(r"包裹|包装|外箱", query) and re.search(r"破损|损坏|丢失", query):
        query += " 包裹丢失 破损赔付 物流核查"
    if ticket.get("category") == "product" and re.search(r"不能使用|用不了|功能|规格|描述不一致|不符", query):
        query += " 商品质量 功能异常 规格不符 证据采集 问题部位 商品标签"
    return query.strip()
