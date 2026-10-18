"""Conservative evidence checks; these are not an independent human semantic rating."""

import re

NON_REAL_FUNDS_NOTICE = "本项目不处理真实资金，不产生真实扣款或资金到账。"


def has_funds_disclosure(draft: str) -> bool:
    """Accept legacy wording or the explicit non-real-money notice; never assume real settlement."""
    return bool(re.search(r"模拟|演示", draft)) or NON_REAL_FUNDS_NOTICE in draft

NEGATION = re.compile(r"尚未|还未|未曾|没有|并未|不能|不会|不代表|不表示|未执行")
ATTRIBUTION = re.compile(r"您(?:反馈|描述|表示|提到|自述)|根据您|按您|您说")
PENDING = re.compile(r"核验后|核实后|核实通过|审核通过后|审批通过后|批准后|符合.{0,8}条件|需.{0,5}(核实|核验|审核|审批)|待.{0,5}(审核|审批)|申请|建议")
TIME = re.compile(r"(?:\d+|[一二三四五六七八九十]+)\s*(?:(?:至|到|[-～~])\s*(?:\d+|[一二三四五六七八九十]+))?\s*(?:个)?\s*(?:工作日|小时|分钟|天)")
ACTION = re.compile(r"联系承运商|(?:与|向)承运商.{0,10}(?:核实|核对|调查)|催促承运商|优先催单|发起(?:优先)?催单|发起.{0,4}调查|创建.{0,4}核查单|核实.{0,8}(?:出库记录|支付流水)|核对.{0,8}出库记录|发放.{0,4}(?:关怀券|优惠券)|补发|退款|退回|核实.{0,5}照片|确认.{0,4}(?:损坏|破损)")
PRESENT = re.compile(r"已经|现已|已为您|已帮您|已替您|我们已|正在|正为您|已(?:优先|联系|核实|核对|发起|创建|补发|退款|退回|发放|确认)")
UI_LABELS = {"问题已解决", "仍需帮助", "新咨询", "提交问题", "采用", "改写", "发送回复", "客服工作台", "客户模拟端", "商城", "我的订单", "售后处理"}


def clauses(text):
    return [part.strip() for part in re.split(r"[。；\n]|但是|但(?!凡)", text) if part.strip()]


def normalize_time(value):
    return re.sub(r"\s|个", "", value).translate(str.maketrans("一二三四五六七八九", "123456789")).replace("-", "至").replace("～", "至").replace("~", "至").replace("到", "至")


def check_reply_evidence(ticket, context, citations, draft):
    evidence = "\n".join(item.get("snippet", "") for item in citations)
    unavailable = context.get("context_available") is False or bool(context.get("tool_fallback"))
    operations = [operation for operation in ticket.get("completed_operations", [])
                  if operation.get("result", {}).get("success")
                  and operation.get("result", {}).get("order_id") == ticket.get("order_id")]
    refund_done = any(operation.get("tool_name") == "execute_refund" for operation in operations)
    simulated = any(operation.get("result", {}).get("mode") == "simulated" for operation in operations)
    checks = {"action_state_has_evidence": True, "customer_claims_attributed": True,
              "fallback_not_claimed_verified": True, "time_promise_supported": True,
              "ui_entry_supported": True, "refund_promise_conditional": True,
              "simulated_result_disclosed": not simulated or has_funds_disclosure(draft)}
    supported_times = {normalize_time(value.group()) for value in TIME.finditer(evidence)}
    if context.get("tool_source") == "shop_database":
        supported_times.update(f"{context[key]}天" for key in ("expected_delivery_days", "transit_days") if key in context)
    for clause in clauses(draft):
        attributed = bool(ATTRIBUTION.search(clause))
        for action in ACTION.finditer(clause):
            before = clause[max(0, action.start() - 18):action.start()]
            after = clause[action.end():action.end() + 10]
            if action.group() == "退款" and re.search(r"收到|受理", before) and re.search(r"申请|咨询|反馈|请求", after):
                continue
            if action.group() == "退款" and re.match(r"到账(?:政策|时效|时间)|政策|规则|进度|状态|时效|咨询", after):
                continue
            if NEGATION.search(before) or attributed:
                continue
            marked = PRESENT.search(before + action.group())
            if marked:
                is_refund = action.group() in {"退款", "退回"}
                if not (is_refund and refund_done):
                    checks["action_state_has_evidence"] = False
        if not attributed:
            if unavailable and re.search(r"已查询|已查到|经查询|经核实|已核实|系统显示|根据.{0,6}记录", clause) and not NEGATION.search(clause):
                checks["fallback_not_claimed_verified"] = False
            if context.get("order_status") == "delayed" and re.search(r"(?:您的|订单|商品).{0,12}(?:已签收|已送达|签收.{0,4}天|已完成)", clause):
                checks["customer_claims_attributed"] = False
            if re.search(r"已收到.{0,10}(?:照片|视频|凭证)|(?:商品|情况).{0,6}符合退款条件|已(?:核实|确认).{0,6}(?:质量|破损|损坏)", clause) and not PENDING.search(clause):
                checks["customer_claims_attributed"] = False
        if re.search(r"预计|将在|会在|保证|承诺|一定|审核.{0,8}完成", clause):
            for duration in TIME.finditer(clause):
                if normalize_time(duration.group()) not in supported_times:
                    checks["time_promise_supported"] = False
            if re.search(r"(?:审核|审批).{0,12}(?:完成|处理|预计)", clause) and TIME.search(clause):
                # Arrival time after approval does not specify approval processing time.
                if not re.search(r"(?:审核|审批).{0,8}(?:耗时|时限|处理时间)", evidence) and not re.search(r"(?:审核|审批)通过后", clause):
                    checks["time_promise_supported"] = False
            if re.search(r"2\s*小时.{0,10}(?:结果|完成|确认)", clause) and "2小时内发起" in evidence:
                checks["time_promise_supported"] = False
        for label in re.findall(r"【([^】]+)】", clause):
            if label not in UI_LABELS and label != ticket.get("order_id") and label not in evidence:
                checks["ui_entry_supported"] = False
        conditional = re.search(r"核实后|核验后|审核通过后|审批通过后|批准后|(?:若|如|待).{0,12}(?:通过|符合|审核|审批)", clause)
        if not refund_done and re.search(r"退款.{0,12}(?:将|会|预计).{0,10}(?:到账|返回|退回)|退款金额.{0,10}将原路", clause) and not conditional:
            checks["refund_promise_conditional"] = False
    return checks


def safe_review_reply(ticket=None):
    from app.services.customer_reply import human_handoff_reply

    reply = human_handoff_reply()
    # A nicer fallback must not hide the nature of a completed non-real-money operation.
    ticket = ticket or {}
    if any(operation.get("result", {}).get("success")
           and operation.get("result", {}).get("order_id") == ticket.get("order_id")
           and operation.get("result", {}).get("mode") == "simulated"
           for operation in ticket.get("completed_operations", [])):
        reply += NON_REAL_FUNDS_NOTICE
    return reply
