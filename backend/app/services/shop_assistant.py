"""Read-only assistant tools backed by the same shop data the user sees."""
import re
from sqlalchemy import select
from app.models import Product, StoreOrder
from app.services.store import STATUS_LABELS, shipping_days

PRODUCT_DETAIL_WORDS = re.compile(
    r"是(?:什么|啥)|(?:什么|啥)(?:东西|商品|产品)|介绍|说说|讲讲|解释|详情|详细|具体|"
    r"长什么样|什么样|干嘛|干啥|干什么用|做什么用|有啥用|有什么用|用途|材质|成分|配料|型号|尺寸|规格|功能|怎么用|如何用|使用方法|区别")


def shop_context(session, ticket, conversation):
    if ticket.get("channel") != "web":
        return None  # Keep explicit legacy evaluation adapters isolated from the shop.
    workspace = ticket.get("workspace_id", "default")
    owner = ticket.get("owner_id")
    latest = ticket.get("message", "")
    promotion_pattern = (
        r"优惠券|折扣券|代金券|满减券|领券|用券|有(?:什么|哪些|可用|能用的)?券|券码|优惠码|优惠|折扣|打折|满减|促销|活动价|特价|"
        r"(?:商城|店铺|店里|你们|你家|咱们).{0,6}活动|^(?:现在|今天|最近)?有(?:什么|哪些|啥)?活动[吗么嘛？?\s]*$")
    promotion_request = bool(re.search(promotion_pattern, latest))
    customer_messages = [m.get("content", "") for m in conversation if m.get("role") == "customer"]
    previous = customer_messages[:-1] if customer_messages and customer_messages[-1] == latest else customer_messages
    if not promotion_request and re.search(r"怎么[领用]|哪里领|在哪[领用]|如何[领用]|使用条件|有效期|满多少|叠加|能领吗", latest):
        # Only inherit the immediately preceding promotion question, not a stale
        # promotion topic across an intervening product/order conversation.
        promotion_request = bool(previous and re.search(promotion_pattern, previous[-1]))
    # Refunds and concrete after-sales issues retain their existing support path.
    promotion_request = promotion_request and not bool(re.search(
        r"退款|退货|退钱|退换|售后|损坏|破损|坏了|缺件|错发|投诉|维修", latest))
    products = session.scalars(select(Product).where(Product.workspace_id == workspace, Product.active.is_(True)).order_by(Product.price_cents, Product.id)).all()
    def matches_query(product, text):
        name = product.name.lower()
        query = text.lower()
        tokens = re.findall(r"[a-z0-9]+", name)
        pairs = {name[i:i+2] for i in range(len(name)-1) if re.fullmatch(r"[\u4e00-\u9fff]{2}", name[i:i+2])}
        pairs -= {"测试", "商品", "演示", "验证"}
        return name in query or any(token in query for token in tokens) or any(pair in query for pair in pairs)
    def product_nouns(text):
        return [word for word in ["耳机", "鼠标", "键盘", "杯子", "衣服", "电脑", "手机", "书包", "水杯"] if word in text]
    shopping_words = r"在售|有卖|想买|购买|库存|商城|哪款|哪些商品"
    other_business = promotion_request or bool(re.search(r"退款|退货|退钱|退换|售后|损坏|破损|坏了|缺件|错发|投诉|维修|转人工|人工客服|物流|配送|发货|快递|签收|订单|支付|付款|扣款|账号|密码|登录|会员|政策|规则", latest))
    explicit_product = bool(product_nouns(latest) or any(matches_query(p, latest) for p in products))
    scope_shift = not explicit_product and bool(re.search(r"Python|编程|作文|写作|旅游|学习计划|生活规划", latest, re.I))
    followup = not other_business and not scope_shift and bool(re.search(
        r"这[个款两些]|那[个款两些]|第[一二三四五六七八九十\d]|上面|刚才|便宜|价格|多少钱|有货|库存|还有|别的|其他|上架|上新|补货|新的|新款|再.{0,4}推荐|哪个|它|怎么样|怎么用|参数|规格|功能", latest)
        or PRODUCT_DETAIL_WORDS.search(latest))
    detail_request = bool(PRODUCT_DETAIL_WORDS.search(latest))
    plan_request = not other_business and not scope_shift and bool(
        (re.search(r"上架|上新|补货|进货|开售", latest) or
         re.search(r"(?:有|出|卖|发布).{0,3}(?:新款|新品|新的)|(?:新款|新品|新的).{0,8}(?:什么时候有|何时有|什么时候卖)", latest)) and
        re.search(r"什么时候|何时|多久|啥时候|几[点天日]|时间|计划|预计|安排|明天|后天|下周|下个月|会不会", latest))
    route_text = latest
    inherited_topic = False
    # Pick the nearest customer-stated topic, rather than merging every earlier
    # product. Assistant suggestions must not silently change the user's focus.
    if followup and not explicit_product:
        previous = customer_messages[:-1] if customer_messages and customer_messages[-1] == latest else customer_messages
        for message in reversed(previous[-10:]):
            if re.search(r"退款|退货|售后|物流|订单|支付|账号|密码|Python|编程|旅游|作文", message, re.I):
                break  # A genuine topic change must not resurrect an older catalog.
            if product_nouns(message) or any(matches_query(p, message) for p in products) or re.search(shopping_words, message):
                route_text = f"{message} {latest}"
                inherited_topic = True
                break
    catalog_request = bool(re.search(shopping_words, route_text)) or plan_request or inherited_topic
    matches = [p for p in products if matches_query(p, route_text)]
    exact_matches = [p for p in products if p.name.lower() in route_text.lower()]
    nouns = product_nouns(route_text)
    if detail_request and exact_matches:
        # Merchant-defined names need no hard-coded category noun or search verb.
        matches = exact_matches
    elif nouns:
        matches = [p for p in products if p in exact_matches or any(word in p.name + p.description for word in nouns)]
    if detail_request and (matches or nouns or inherited_topic or ticket.get("category") == "product"
                           or re.search(r"商品|产品|商城|店铺", latest)):
        catalog_request = True
    if (matches or nouns) and re.search(r"找|挑|搜|看看|看一下|看下|有没有|有货|推荐|对比|比较|价格|多少钱|便宜|买|选|功能|参数|规格", route_text):
        catalog_request = True
    if not matches and not nouns and catalog_request and re.search(r"哪些|推荐|商城|所有|对比|比较|便宜", route_text):
        matches = products[:20]
    if matches and re.search(r"买|选|怎么用|如何用|参数|功能|规格|适合", latest) and not re.search(r"退款|退货|物流|订单", latest):
        catalog_request = True
    if other_business or scope_shift:
        catalog_request = False
    base = {"customer_name": ticket["customer_name"], "customer_level": ticket.get("customer_tier", "standard"),
            "order_id": ticket.get("order_id"), "order_status": "unknown", "order_amount": None, "paid": False,
            "context_available": False, "risk_flags": [], "tool_source": "shop_database", "tool_attempts": 0,
            "tool_retry_count": 0, "tool_errors": [], "assistant_route": "catalog" if catalog_request else "support",
            "catalog": [{"id": p.id, "name": p.name, "description": p.description[:500], "category": p.category, "price": p.price_cents / 100,
                         "stock": p.stock} for p in matches[:20]], "catalog_query": route_text[-1500:],
            "catalog_topic": "、".join(nouns) if nouns else (matches[0].name if len(matches) == 1 else "相关商品"),
            "catalog_intent": "availability_plan" if plan_request else "details" if detail_request else "search",
            "catalog_detail_question": latest if detail_request else "",
            "availability_plan_available": False}
    if promotion_request and not scope_shift:
        base["assistant_route"] = "promotions"
        # Product prices and historical compensation tools do not prove there is
        # a customer-claimable coupon or an active promotional campaign.
        base["promotion_data_available"] = False
    # Greetings and non-commerce messages receive a fixed service-scope reply,
    # not unrestricted general-purpose model answers.
    if base["assistant_route"] == "support" and ticket.get("category") == "other" and not re.search(
            r"订单|退款|退货|物流|快递|支付|付款|扣款|商品|商城|会员|账号|政策|售后|人工客服|客服人员", latest):
        base["assistant_route"] = "service_scope"
    stmt = select(StoreOrder).where(StoreOrder.workspace_id == workspace)
    if owner:
        stmt = stmt.where(StoreOrder.owner_id == owner)
    elif ticket.get("customer_owned"):
        return base  # Never infer ownership from a display name.
    order = None
    if ticket.get("order_id"):
        order = session.scalar(stmt.where(StoreOrder.order_no == ticket["order_id"]))
    elif re.search(r"最近.*订单|最新.*订单|我的订单|我的物流|物流状态", latest):
        rows = session.scalars(stmt.order_by(StoreOrder.created_at.desc()).limit(5)).all()
        base["own_orders"] = [{"order_no": o.order_no, "product": o.product_name, "status": STATUS_LABELS[o.status]} for o in rows]
        if len(rows) == 1 or re.search(r"最近|最新", latest):
            order = rows[0] if rows else None
    if order:
        base.update(order_id=order.order_no, order_status=order.status, order_status_label=STATUS_LABELS[order.status],
                    order_amount=(order.total_cents - order.refunded_cents) / 100,
                    paid=order.status not in {"pending_payment", "cancelled"}, context_available=True,
                    product_name=order.product_name, expected_delivery_days=order.expected_delivery_days,
                    transit_days=shipping_days(order), overdue=order.status == "in_transit" and shipping_days(order) > order.expected_delivery_days)
        if re.search(r"物流|配送|送达|签收|发货|快递|到哪|到了|何时到", latest) and base["assistant_route"] == "support":
            base["assistant_route"] = "logistics"
    return base


def needs_order_context(ticket):
    text = ticket.get("message", "")
    if re.search(r"政策|规则|条件|规定|FAQ|营业|上班|客服.{0,4}(时间|几点)", text):
        return False
    return ticket.get("category") in {"logistics", "refund", "payment"} or bool(re.search(r"订单|损坏|缺件|错发|補发|补发|收到.{0,6}商品", text))


def handoff_reasons(ticket, conversation, context):
    from app.services.reply_validation import requests_money_operation
    latest = ticket.get("message", "")
    reasons = []
    if requests_money_operation(ticket) or (re.search(r"退款|退货|退钱", latest) and not re.search(r"政策|条件|规定|是否支持|规则", latest)):
        reasons.append("退款或资金操作需人工处理")
    if context.get("overdue"):
        reasons.append("运输天数超过预计送达天数")
    if re.search(r"转人工|人工客服|找人工|找真人|真人客服|转接.{0,4}客服|客服人员", latest) and not re.search(r"不[要用需].{0,4}人工|无需人工", latest):
        reasons.append("用户要求转人工")
    if not re.search(r"条件|规则|政策|怎么|如何|能否|是否|能不能", latest) and re.search(
            r"(?:给我|为我|帮我|直接|请|请给我|请为我|麻烦给我|我要|我想)(?:直接|立即|马上)?"
            r"(?:发放|发|生成|创建).{0,6}(?:优惠券|关怀券|代金券)|"
            r"(?:优惠券|关怀券|代金券).{0,4}(?:发给我|发放给我)", latest):
        reasons.append("发券等优惠写操作需人工处理")
    return reasons


def service_scope_reply(ticket, policy):
    """Describe supported services without generating out-of-domain content."""
    from app.schemas import ResolutionResult
    message = ticket.get("message", "")
    if re.search(r"^\s*(您好|你好|hello|hi|在吗|早上好|晚上好)[！!。？?\s]*$", message, re.I):
        reply = "您好！我是 ResolveAI 电商智能客服。您可以直接咨询商品选购、价格库存、订单支付、物流配送、退换货或账号问题，不需要先填写订单表单。"
    elif re.search(r"能做什么|能帮|可以帮|您是谁|你是谁|介绍.*自己", message):
        reply = "我可以帮您查找本商城在售商品、比较价格、查看您自己的订单与物流，并协助处理支付、退换货和账号问题。您直接描述购物需求即可，涉及退款等需要人工处理的事项会转人工客服。"
    else:
        reply = "我主要提供本商城的电商客服服务，暂不提供与购物无关的通用问答。您可以咨询商品选购、价格库存、订单支付、物流配送、退换货或账号问题；请告诉我您具体想了解哪方面。"
    return ResolutionResult(recommended_action="说明电商客服服务范围并引导购物咨询", rationale="服务范围文案固定；不生成范围外回答，也不声称已核实业务数据",
                            draft_response=reply, requires_approval=bool(policy.get("must_review")), confidence=1)


def unavailable_catalog_plan_reply(context, policy):
    """The current product schema has no launch/restock schedule; never invent one."""
    from app.schemas import ResolutionResult
    topic = context.get("catalog_topic", "相关商品")
    reply = (f"您好，目前商城没有记录{topic}的上新或补货计划，暂时无法确认具体时间。"
             "您可以后续查看商城的上新情况，也可以告诉我您的预算和需求，我帮您查找当前在售的其他商品。")
    return ResolutionResult(recommended_action="说明上新或补货时间未知，继续协助选购",
                            rationale="系统无上新/补货计划记录，不猜测时间，也不因普通信息未知转人工",
                            draft_response=reply, requires_approval=bool(policy.get("must_review")), confidence=1)


def catalog_details_reply(context, policy):
    """Explain only recorded product facts, with clarification rather than invented specifications."""
    from app.schemas import ResolutionResult
    items = context.get("catalog", [])
    if len(items) == 1:
        item = items[0]
        name, description = item["name"], item.get("description", "").strip()
        reply = f"您好，“{name}”的介绍是：{description}\n" if description else f"您好，您问的是商城里的“{name}”。\n"
        if item.get("category") and item["category"] != "其他":
            reply += f"商品分类：{item['category']}。"
        reply += f"售价 {item['price']:.2f} 元，库存 {item['stock']} 件。"
        if not item["stock"]:
            reply += "目前暂时缺货，不能下单。"
        # A description that merely repeats the name cannot establish what the item is.
        sparse = not description or bool(re.fullmatch(
            rf"\s*(?:这是|本品是|本商品是)?\s*(?:一|1)?(?:款|件|个|颗|种|台|套|只|盒)?\s*{re.escape(name)}[。.!！\s]*", description))
        if sparse:
            question = context.get("catalog_detail_question", "")
            missing_topic = next((label for pattern, label in [
                (r"成分|配料|原料", "成分"), (r"材质", "材质"), (r"尺寸", "尺寸"),
                (r"容量", "容量"), (r"型号", "型号"), (r"规格|参数", "规格参数"),
                (r"怎么用|如何用|使用方法", "使用方法"), (r"用途|有啥用|有什么用|干嘛|干啥|干什么用|做什么用", "用途"),
                (r"功能", "功能"),
            ] if re.search(pattern, question)), None)
            reply += (f"\n这款商品暂未提供{missing_topic}说明。" if missing_topic else
                      "\n这款商品暂时没有更详细的介绍。您想了解它的用途、规格，还是其他方面呢？")
        else:
            reply += "\n您想进一步了解它的哪方面呢？"
    elif items:
        rows = [f"{item['name']}：{item.get('description') or '暂无详细介绍'}" for item in items[:5]]
        reply = "您好，找到多件相关商品：\n" + "\n".join(rows) + "\n您具体想了解哪一款？告诉我商品名称就可以。"
    else:
        reply = "您好，您说的是哪一款商品呢？请告诉我商城中的完整商品名称，我帮您查看。"
    return ResolutionResult(recommended_action="依据商城商品资料介绍并澄清缺失信息",
                            rationale="只使用当前在售商品的名称、描述、分类、价格与库存；普通资料缺失不等于售后转人工",
                            draft_response=reply, requires_approval=bool(policy.get("must_review")), confidence=1)


def promotion_faq_citation(session, workspace_id, citations, rag_status):
    """Use a reliable, enabled, in-scope promotion FAQ, never an unrelated coupon mention."""
    from app.core.config import get_settings
    from app.models import KnowledgeDocument
    from app.services.auth import knowledge_scope
    from app.services.security import SecurityService, redact_text

    if not rag_status.get("reliable"):
        return None
    threshold = rag_status.get("threshold", get_settings().rag_min_score)
    for citation in sorted(citations, key=lambda item: item.get("score", 0), reverse=True):
        if citation.get("score", 0) < threshold:
            continue
        # Recheck canonical text, enablement and workspace at the point of use.
        document = session.scalar(select(KnowledgeDocument).where(
            KnowledgeDocument.id == citation.get("id"), KnowledgeDocument.enabled.is_(True),
            knowledge_scope(workspace_id)))
        if not document or not document.content.strip():
            continue
        topic_text = document.title
        if document.source.startswith("faq://"):
            topic_text += "\n" + document.content
        if not re.search(r"优惠|折扣|代金券|满减|促销|活动|领券", topic_text):
            continue
        if re.search(r"退款|退货|售后|物流|延误|赔付|补偿|关怀", document.title):
            continue  # A compensation SOP is not a general claimable-coupon offer.
        if SecurityService().assess(subject=document.title, message=document.content).blocked:
            continue  # Knowledge text is data, not permission to bypass the system.
        return {"id":document.id, "title":redact_text(document.title), "source":document.source,
                "snippet":redact_text(document.content.strip()), "score":citation["score"]}
    return None


def promotion_information_reply(policy, faq=None):
    """Prefer a verified knowledge answer; only use 'unknown' when there is no usable FAQ."""
    from app.schemas import ResolutionResult
    if faq:
        reply = (f"您好，商家知识库《{faq['title']}》中的说明是：\n{faq['snippet']}\n"
                 "以上为商家公布的优惠说明，本轮未执行领券或发券操作。")
        return ResolutionResult(recommended_action="依据商家优惠知识回复咨询",
                                rationale=f"采用已启用且达到检索阈值的知识文档：{faq['title']}（{faq['source']}）；仅说明规则，不执行发券",
                                draft_response=reply, requires_approval=bool(policy.get("must_review")),
                                confidence=faq["score"])
    reply = ("您好，优惠券和促销活动属于商城咨询范围。"
             "目前暂时无法查询到可领取优惠券、促销活动及使用规则的可靠信息，不能确认是否有可用优惠。"
             "商品价格请以商城当前显示为准。您可以告诉我想购买的商品和预算，我可以帮您查询售价、库存或比较价格。")
    return ResolutionResult(recommended_action="说明优惠信息暂不可核实，继续协助选购",
                            rationale="当前未接入可领取优惠券或促销活动数据，不推断无优惠，不编造金额、规则或领取入口，不执行发券",
                            draft_response=reply, requires_approval=bool(policy.get("must_review")), confidence=1)


def demo_shop_reply(ticket, context, policy):
    from app.schemas import ResolutionResult
    route = context.get("assistant_route")
    if route == "service_scope":
        return service_scope_reply(ticket, policy)
    if route == "promotions":
        return promotion_information_reply(policy)
    if route == "catalog":
        if context.get("catalog_intent") == "availability_plan" and not context.get("availability_plan_available"):
            return unavailable_catalog_plan_reply(context, policy)
        if context.get("catalog_intent") == "details":
            return catalog_details_reply(context, policy)
        items = context.get("catalog", [])
        if items:
            rows = [f"{p['name']}：{p['price']:.2f} 元，库存 {p['stock']} 件。{p['description']}" for p in items[:5]]
            reply = "您好，当前商城在售商品信息如下：\n" + "\n".join(rows)
            if len(items) >= 2:
                available = [p for p in items if p["stock"] > 0]
                if available:
                    cheap = min(available, key=lambda p: p["price"])
                    reply += f"\n其中有库存的最低价商品是 {cheap['name']}，价格为 {cheap['price']:.2f} 元。您可以结合描述与需求选择，并在商城查看后模拟下单。"
        else:
            reply = "您好，当前商城没有找到与您描述匹配的在售商品。您可以告诉我更具体的商品名称、用途或规格，我再帮您查询；未上架的商品不能直接购买。"
        return ResolutionResult(recommended_action="查询在售商品与比较价格", rationale="依据商城数据库的在售商品、价格和库存回答", draft_response=reply,
                                requires_approval=bool(policy.get("must_review")), confidence=1)
    if route == "logistics":
        reply = f"您好，订单 {context['order_id']} 的当前状态为{context['order_status_label']}。"
        if context["order_status"] == "in_transit":
            reply += f"模拟运输已进行 {context['transit_days']} 天，订单记录的预计运输时间为 {context['expected_delivery_days']} 天。"
        if context.get("overdue"):
            reply += "运输时间已超过订单预计送达天数，需要人工客服继续处理。"
        else:
            reply += "以上为商城当前记录，您可在我的订单中查看各阶段进度。"
        return ResolutionResult(recommended_action="查询物流状态", rationale="以实际模拟订单记录为依据", draft_response=reply,
                                requires_approval=bool(policy.get("must_review")), confidence=1)
    return None
