"""Distinguish general shopping instructions from requests to act on a real record."""

import re


_GUIDANCE = re.compile(r"怎么|如何|怎样|咋|在哪|哪里|哪儿|步骤|流程|方法|使用说明|操作说明|政策|规则|条件|支付方式|服务时间|营业时间")
_COMMAND = re.compile(r"帮我|替我|为我|给我|退我|我要(?!了解|知道)|我要求|我申请|我想(?:要|申请|办理|退|换|赔)|请(?:你|您)?(?:帮|替|为)|立即|立刻|直接|马上|现在就|执行|跳过|绕过")
_NONCOMMERCE = re.compile(r"Python|编程|作文|写作|旅游|学习计划|生活规划", re.I)


def is_guidance_question(message: str) -> bool:
    if not _GUIDANCE.search(message) or _COMMAND.search(message):
        return False
    # Asking how to move a concrete sum is not a generic refund-policy FAQ.
    if re.search(r"退款|退钱|返还|赔付", message) and re.search(r"\d+(?:\.\d+)?\s*元", message):
        return False
    return True


def allows_manual_faq(message: str) -> bool:
    if _NONCOMMERCE.search(message):
        return False
    if is_guidance_question(message):
        return True
    # An FAQ is not evidence of current stock, a customer's delivery or payment.
    if re.search(r"ORD-|\d{2}-\d{2}-\d{2}-\d+|我的.{0,8}(?:订单|包裹|快递|物流|退款)|"
                 r"(?:订单|包裹|快递).{0,10}(?:到哪|到了|发货|送达|签收|状态|进度)|"
                 r"发货(?:了)?[吗没]|到账(?:了)?[吗没]|多少钱|售价|当前价格|库存|有货吗|在售|帮我找|帮我查|重复扣款|多扣", message):
        return False
    return True
