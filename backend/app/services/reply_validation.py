"""Deterministic checks for facts/actions a draft is not allowed to invent."""

import re


def requests_money_operation(ticket: dict) -> bool:
    latest = re.sub(r"(?:不|不要|无需|暂不)(?:再|再次)?(?:申请|办理|发起|执行)(?:新的|新|再次|再)?(?:退款|退货|退还|返还)", "", ticket.get("message", ""))
    if re.search(r"只是查询|退款进度|到账时间|到账时效|多久到账", latest) and not re.search(r"(我要|申请|直接|请求|选择|希望).{0,6}(退款|退还|返还)", latest):
        return False
    text = f"{ticket.get('subject', '')} {latest}"
    if re.search(r"(查询|核对|查看|了解).{0,8}(退款进度|退款到账|到账时间)", text) and not re.search(r"(申请|直接|我要|选择).{0,6}(退款|返还|退还)", text):
        return False
    if re.search(r"(我想|我要|希望|选择|申请|直接|请给|给我|退给|要求).{0,12}(退款|退货|退钱|退回|返还|赔付)", text):
        return True
    if re.search(r"(退款|退货).{0,6}(申请|请求)|返还.{0,6}扣款", text):
        return True
    if re.search(r"(请|麻烦).{0,6}(返还|退还|退回)", text):
        return True
    return False


def extract_order_identifier(message: str) -> str | None:
    match = re.search(r"订单(?:编号|号)\s*(?:为|是|[:：])?\s*([A-Za-z0-9][A-Za-z0-9_-]{1,29})(?![A-Za-z0-9_-])", message)
    if match:
        return match.group(1)
    match = re.search(r"(?<![A-Za-z0-9_-])(ORD-[A-Za-z0-9_-]{1,26})(?![A-Za-z0-9_-])", message)
    return match.group(1) if match else None


def check_draft_facts(ticket: dict, context: dict, policy: dict, resolution: dict, citations: list[dict] | None = None) -> dict[str, bool]:
    draft = resolution.get("draft_response", "")
    unavailable = context.get("context_available") is False
    order_ids = re.findall(r"(?<![A-Za-z0-9_-])ORD-[A-Za-z0-9_-]+", draft)
    expected_order = ticket.get("order_id") or context.get("order_id")
    amount = resolution.get("refund_amount")
    limit = policy.get("max_refund")
    # Creating an analysis does not contact carriers, verify photos, or execute money tools.
    execution_claims = re.findall(
        r"(已经|已为您|已帮您|已替您|我们已(?:经)?)(?:为您|帮您|替您)?(发起(?:优先)?(?:催单|调查)|发放|补发|退款|退回|联系承运商|核实.{0,4}照片|确认.{0,4}破损)",
        draft,
    )
    verified_refunds = [operation["result"] for operation in ticket.get("completed_operations", [])
                       if operation.get("tool_name") == "execute_refund"
                       and operation.get("result", {}).get("success")
                       and operation.get("result", {}).get("order_id") == expected_order]
    execution_supported = all(
        action in {"退款", "退回"} and verified_refunds
        and all(result.get("mode") != "simulated" or re.search(r"模拟|演示", draft)
                for result in verified_refunds)
        for _, action in execution_claims
    )
    approval_claim = bool(re.search(r"(本次|当前|您的|由于|因为).{0,24}(需要人工审[核批]|金额.{0,6}(较大|较高|超过).{0,16}审批)", draft))
    checks = {
        "order_reference_supported": not order_ids or bool(expected_order) and all(
            value.rstrip(".,。，；") == expected_order for value in order_ids
        ),
        "missing_order_not_claimed_verified": not unavailable or not re.search(r"已核验订单|已查询到|查询到您的订单|已为您找到关联订单", draft),
        "no_unexecuted_action_claim": bool(execution_supported),
        "refund_amount_supported": amount is None or (
            requests_money_operation(ticket) and limit is not None and 0 <= amount <= limit
        ),
        "approval_statement_supported": not approval_claim or bool(
            policy.get("must_review") or requests_money_operation(ticket)
        ),
    }
    if citations is not None:
        from app.services.reply_guard import check_reply_evidence
        checks.update(check_reply_evidence(ticket, context, citations, draft))
    return checks
