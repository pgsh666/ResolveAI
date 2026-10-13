from datetime import timedelta

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models import Approval, KnowledgeDocument, Ticket, utc_now
from app.services.embeddings import EmbeddingService


KNOWLEDGE_BASE = [
    {
        "title": "七天无理由退货政策",
        "source": "policy://after-sales/7-day-return",
        "category": "refund",
        "content": "签收后7天内，商品完好且不影响二次销售可申请无理由退货。质量问题由平台承担运费。退款原路返回，通常1至3个工作日到账。",
    },
    {
        "title": "退款审批与风控规范",
        "source": "policy://risk/refund-approval",
        "category": "refund",
        "content": "单笔退款金额不高于100元且无风险标记可自动执行。超过100元、命中近期退款风险或订单信息不一致时，必须进入人工审批并记录审批人。",
    },
    {
        "title": "物流延误补偿标准",
        "source": "policy://logistics/delay-care",
        "category": "logistics",
        "content": "超过承诺送达时间24小时应发起优先催单；普通用户可发放10元关怀券，VIP用户可发放20元关怀券。超过72小时可升级物流专员。",
    },
    {
        "title": "重复扣款处理流程",
        "source": "sop://payment/duplicate-charge",
        "category": "payment",
        "content": "先核验支付流水和订单数量。确认重复扣款后，重复部分应原路退回。任何资金操作都要写入审计记录，异常流水转交支付专员。",
    },
    {
        "title": "客服沟通语气指南",
        "source": "guide://service/tone",
        "category": "general",
        "content": "回复应先表达理解，再说明已完成的核验，最后给出明确动作和预计时间。避免推诿和绝对承诺；对愤怒用户提供清晰升级路径。",
    },
    {
        "title": "退款到账时效与异常排查",
        "source": "sop://refund/arrival-troubleshooting",
        "category": "refund",
        "content": "退款审核通过后，余额与优惠券通常实时退回，银行卡及第三方支付通常需要1至3个工作日。超过预计时效时，应先核对退款流水号、支付渠道和银行处理状态；退款失败或原支付账户异常时转交支付专员处理，不得要求客户提供完整银行卡号。",
    },
    {
        "title": "质量问题退换货规范",
        "source": "policy://after-sales/quality-return",
        "category": "refund",
        "content": "商品存在破损、功能故障或与描述明显不符时，客户可申请退货、换货或维修。客服应记录问题描述和必要凭证，审核通过后由平台承担合理运费。涉及人身安全、批量故障或高价值商品时必须升级售后专员。",
    },
    {
        "title": "特殊商品售后限制",
        "source": "policy://after-sales/special-goods",
        "category": "refund",
        "content": "定制商品、已拆封数字内容、鲜活易腐商品及影响二次销售的贴身用品通常不适用七天无理由退货。若商品本身存在质量问题，仍应按质量售后流程处理。拒绝申请时必须说明具体商品类型和适用条款。",
    },
    {
        "title": "包裹丢失与破损赔付流程",
        "source": "sop://logistics/lost-damaged-package",
        "category": "logistics",
        "content": "物流超过7天无轨迹、承运商确认丢件或客户收到明显破损包裹时，应创建物流核查单。核实后可选择补发或退款；高价值商品、签收争议及多次索赔账户需要人工复核。补偿结果与物流凭证必须写入工单审计记录。",
    },
    {
        "title": "收货地址修改与物流拦截",
        "source": "sop://logistics/address-intercept",
        "category": "logistics",
        "content": "订单未出库时可核验身份后修改收货地址；已出库订单只能尝试联系承运商拦截或改派，不得承诺一定成功。涉及跨城市改址、高价值商品或收件人信息整体变更时，应取消原单并建议客户重新下单。",
    },
    {
        "title": "签收争议核验标准",
        "source": "policy://logistics/delivery-dispute",
        "category": "logistics",
        "content": "客户反馈显示签收但未收到时，应核对签收时间、签收方式、配送照片及代收点信息，并在2小时内发起承运商调查。未经核验不得直接认定客户或骑手责任；生鲜、药品等时效敏感订单优先补发并同步调查。",
    },
    {
        "title": "支付失败与冻结款处理",
        "source": "sop://payment/failed-frozen",
        "category": "payment",
        "content": "支付失败但账户显示扣款时，先区分实际入账与银行预授权冻结。未生成支付流水的冻结款通常会在1至7个工作日自动释放；存在成功流水但订单未创建时，转交支付专员对账。客服不得要求客户重复支付以验证问题。",
    },
    {
        "title": "电子发票开具与红冲规范",
        "source": "policy://payment/invoice",
        "category": "payment",
        "content": "订单完成后可按实际支付金额申请电子发票。抬头、税号等信息应由客户确认；发票已开具后需要修改时，先完成原发票红冲再重新开具。退款订单仅对未退款的实际支付金额保留开票资格。",
    },
    {
        "title": "组合支付退款拆分规则",
        "source": "policy://payment/mixed-refund",
        "category": "payment",
        "content": "订单使用银行卡、余额、积分或优惠券组合支付时，退款按原支付构成分别退回。现金部分原路返还，积分按有效期规则退回账户，优惠券是否返还取决于活动条款。客服应分别说明各部分金额与预计到账时间。",
    },
    {
        "title": "账号登录与密码重置流程",
        "source": "sop://account/login-reset",
        "category": "account",
        "content": "登录失败时依次检查账号状态、验证码时效、设备时间和客户端版本。密码重置必须通过已绑定手机或邮箱完成；无法使用原验证方式时进入人工身份核验，不得通过客服直接查询、修改或告知用户密码。",
    },
    {
        "title": "账号安全与身份核验规范",
        "source": "policy://account/security-verification",
        "category": "account",
        "content": "修改绑定手机、关闭二次验证、查询敏感交易或处理疑似盗号时，必须完成增强身份核验。客服仅收集完成核验所必需的信息，不得索取密码、短信验证码、支付密码或完整证件号码。疑似盗号账户应立即限制高风险操作并升级安全团队。",
    },
    {
        "title": "会员等级与权益补发规则",
        "source": "policy://account/membership-benefits",
        "category": "account",
        "content": "会员等级依据有效成长值自动计算，退款可能导致成长值回退。权益未到账时应核验领取条件、有效期和使用记录；确认系统异常后可补发同等权益。人工调整等级或补发高价值权益需要主管审批。",
    },
    {
        "title": "商品缺件与错发处理流程",
        "source": "sop://product/missing-wrong-item",
        "category": "product",
        "content": "收到商品缺少配件、数量不足或型号错发时，应核对订单明细、出库记录和客户提供的必要凭证。确认后优先补发正确商品或缺失配件；客户不愿等待时可按未履约部分退款。高价值或连续异常订单进入人工复核。",
    },
    {
        "title": "商品质量问题证据采集指南",
        "source": "guide://product/quality-evidence",
        "category": "product",
        "content": "处理外观破损、功能异常或规格不符时，只收集判断责任与解决问题所需的最少材料，例如整体照片、问题部位和商品标签。不得反复要求客户提交相同材料；明显可判断的问题应先给方案，再由内部完成责任追溯。",
    },
    {
        "title": "客服工单 SLA 与升级机制",
        "source": "policy://service/sla-escalation",
        "category": "general",
        "content": "紧急、高、中、低优先级工单的首次响应目标分别为15、30、60和120分钟。涉及资金安全、人身安全、舆情风险或同一问题重复联系三次以上时立即升级。升级后原处理人仍需跟踪结果，不得以已转交为由中断沟通。",
    },
    {
        "title": "用户隐私与敏感信息处理规范",
        "source": "policy://service/privacy",
        "category": "general",
        "content": "客服仅可在解决当前问题所必需的范围内访问和记录用户信息。密码、短信验证码、支付密码、完整银行卡号等信息禁止索取和保存。工单中出现敏感信息时应脱敏展示，并限制无关人员访问与导出。",
    },
]


TICKET_SEEDS = [
    ("TKT-1048", "林晓雨", "VIP", "退款一直没有到账", "上周退货成功了，但退款还没到账，今天能处理吗？", "urgent", "ORD-8821", 329.0),
    ("TKT-1047", "周航", "standard", "快递三天没有更新", "物流信息停了三天，商品一直没收到，请帮我催一下。", "high", "ORD-7642", 128.0),
    ("TKT-1046", "陈念", "standard", "订单被重复扣款", "我只买了一件商品，银行卡却扣了两次款。", "high", "ORD-6255", 199.0),
    ("TKT-1045", "苏桐", "VIP", "商品尺寸不合适", "衣服尺寸偏小，想在七天内退货。", "medium", "ORD-5190", 459.0),
    ("TKT-1044", "顾言", "standard", "无法登录会员账号", "重置密码后还是提示登录失败。", "medium", None, None),
    ("TKT-1043", "赵可", "standard", "收到的杯子破损", "包裹刚打开就是碎的，想要补发。", "high", "ORD-4388", 89.0),
]


def seed_database(session: Session) -> None:
    embeddings = EmbeddingService()
    existing_sources = set(session.scalars(select(KnowledgeDocument.source)).all())
    for item in KNOWLEDGE_BASE:
        if item["source"] not in existing_sources:
            session.add(
                KnowledgeDocument(
                    **item,
                    embedding=embeddings.legacy_embed(f"{item['title']}\n{item['content']}"),
                )
            )

    ticket_count = session.scalar(select(func.count(Ticket.id))) or 0
    if ticket_count == 0:
        now = utc_now()
        for index, (number, name, tier, subject, message, priority, order_id, amount) in enumerate(
            TICKET_SEEDS
        ):
            session.add(
                Ticket(
                    ticket_no=number,
                    customer_name=name,
                    customer_tier=tier,
                    subject=subject,
                    message=message,
                    priority=priority,
                    order_id=order_id,
                    amount=amount,
                    status="new" if index < 4 else "in_progress",
                    channel=["chat", "email", "app"][index % 3],
                    sla_minutes=15 if priority == "urgent" else (30 if priority == "high" else 60),
                    created_at=now - timedelta(minutes=index * 23 + 8),
                )
            )
    session.commit()


def normalize_legacy_ticket_statuses(session: Session) -> None:
    """Repair tickets that older workflow versions resolved before customer confirmation."""
    legacy_resolved = list(
        session.scalars(select(Ticket).where(Ticket.status == "resolved")).all()
    )
    changed = False

    for ticket in legacy_resolved:
        assessment = dict(ticket.assessment or {})
        confirmation = assessment.get("customer_confirmation") or {}
        if confirmation.get("resolved") is True:
            continue

        approval = session.scalar(select(Approval).where(Approval.ticket_id == ticket.id))
        conversation = assessment.get("conversation") or []
        last_role = conversation[-1].get("role") if conversation else None

        if approval and approval.state == "pending":
            ticket.status = "pending_approval"
        elif last_role == "support" or assessment.get("sent_response"):
            ticket.status = "awaiting_customer"
        elif assessment.get("confidence_breakdown") or ticket.category or ticket.draft_response:
            ticket.status = "in_progress"
        else:
            ticket.status = "new"
        changed = True

    if changed:
        session.commit()
