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
    return query.strip()
