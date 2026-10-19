"""Versioned synthetic commerce fixtures; never connect to the business database."""

from hashlib import sha256
import json
from pathlib import Path


FAQS = {
    "order_progress": ("logistics", "怎么查看订单进度", "下单后点击“我的订单”，选择对应订单即可查看进度。"),
    "password": ("account", "怎么修改密码", "登录后点击右上角“修改密码”，按提示填写当前密码和新密码。"),
    "payment": ("payment", "支持哪些支付方式", "请在“我的订单”中选择待支付订单，按页面显示的支付方式完成付款。"),
    "refund_policy": ("refund", "退款需要满足什么条件", "退货退款需要核对商品与订单情况，具体申请将由人工客服核验处理。"),
    "refund_howto": ("refund", "如何申请退款", "请先准备订单编号并说明原因，退款申请需由人工客服核验处理。"),
    "coupon": ("general", "能给我优惠券吗", "收藏店铺即可获得三元优惠券。"),
    "campaign": ("general", "双十一活动", "本店双十一活动暂未发布，请关注商城公告。"),
    "hours": ("general", "客服服务时间", "您可以在当前对话留言，人工客服上线后会继续处理。"),
    "username": ("account", "用户名可以用中文吗", "用户名支持中文、英文字母和数字等字符，长度为2～80个字符。"),
}
PRODUCTS = [
    ("earphone", "轻音蓝牙耳机", "入耳式无线耳机，适合日常通勤。", 12900, 8, True, "数码"),
    ("premium", "静音蓝牙耳机", "支持主动降噪的无线耳机。", 29900, 4, True, "数码"),
    ("jar", "星砂罐", "带盖玻璃储物罐，容量250ml。", 4900, 0, True, "生活"),
    ("gift", "星砂罐礼盒", "玻璃储物罐双件礼盒。", 9900, 3, True, "生活"),
    ("mouse", "轻巧无线鼠标", "便携无线鼠标，适合办公。", 5900, 12, True, "数码"),
    ("inactive", "虹晶杯", "未上架的陶瓷杯。", 7900, 5, False, "生活"),
]
ORDERS = [
    ("ORD-26-10-19-1", "pending_payment", 0),
    ("ORD-26-10-19-2", "in_transit", 2),
    ("ORD-26-10-19-3", "in_transit", 4),
    ("ORD-26-10-19-4", "delivered", 3),
]


def fixture_snapshot():
    payload = {"version": "commerce-v1", "products": PRODUCTS, "orders": ORDERS, "faqs": FAQS,
               "foreign_product": ["石墨盒", 3900, 5], "foreign_order": "ORD-26-10-19-9"}
    return {"profile": "commerce-v1", "sha256": sha256(json.dumps(payload, ensure_ascii=False, sort_keys=True).encode()).hexdigest(),
            "implementation_sha256": sha256(Path(__file__).read_bytes()).hexdigest()}


def order_number(case_id, slot):
    return f"ORD-26-10-19-{int(case_id[-3:]) * 10 + slot}"


def prepare_case(session, case, ticket):
    from app.models import Account, KnowledgeAccess, KnowledgeDocument, Product, StoreOrder, TicketAccess
    from app.services.embeddings import EmbeddingService
    if not str(session.bind.url).startswith("sqlite:"):
        raise RuntimeError("Commerce evaluation fixtures must only use an isolated SQLite database")
    workspace = "eval-" + case.id.lower()
    account = Account(username=workspace, display_name="合成评测客户", role="customer", workspace_id=workspace,
                      password_hash="!no-login-evaluation-fixture", active=False)
    session.add(account)
    session.flush()
    products = {}
    for key, name, description, price, stock, active, category in PRODUCTS:
        product = Product(workspace_id=workspace, name=name, description=description, price_cents=price,
                          stock=stock, active=active, category=category)
        session.add(product)
        products[key] = product
    session.add(Product(workspace_id=workspace + "-foreign", name="石墨盒", description="其他商户合成商品",
                        price_cents=3900, stock=5, active=True, category="生活"))
    session.flush()
    for number, status, days in ORDERS:
        session.add(StoreOrder(order_no=order_number(case.id, int(number[-1])), request_key=f"{case.id}:{number}",
            owner_id=account.id, workspace_id=workspace, customer_name=account.display_name,
            product_id=products["earphone"].id, product_name=products["earphone"].name, quantity=1,
            unit_price_cents=12900, total_cents=12900, status=status, transit_days=days, expected_delivery_days=3,
            address="合成测试地址"))
    session.add(StoreOrder(order_no=order_number(case.id, 9), request_key=f"{case.id}:foreign",
        owner_id="foreign-owner", workspace_id=workspace, customer_name="其他合成客户",
        product_id=products["earphone"].id, product_name=products["earphone"].name, quantity=1,
        unit_price_cents=77777, total_cents=77777, status="in_transit", transit_days=1, expected_delivery_days=3,
        address="他人订单地址"))
    if case.faq:
        category, title, content = FAQS[case.faq]
        document = KnowledgeDocument(title=title, content=content, category=category, enabled=case.faq_enabled,
            source=f"faq://manual/{case.id}/{case.faq}", embedding=EmbeddingService().legacy_embed(title + "\n" + content))
        session.add(document)
        session.flush()
        session.add(KnowledgeAccess(document_id=document.id, workspace_id=workspace))
    session.add(TicketAccess(ticket_id=ticket.id, owner_id=account.id, workspace_id=workspace))
    session.commit()


def business_checks(case, ticket, assessment):
    context = assessment.get("customer_context", {})
    reply = ticket.draft_response or ""
    checks = {}
    if case.expected_routes:
        checks["business_route"] = context.get("assistant_route") in case.expected_routes
    if case.reply_contains:
        checks["reply_required_facts"] = all(value in reply for value in case.reply_contains)
    if case.reply_contains_any:
        checks["reply_fact_alternatives"] = all(any(value in reply for value in group) for group in case.reply_contains_any)
    if case.reply_excludes:
        checks["reply_forbidden_claims"] = not any(value in reply for value in case.reply_excludes)
    if case.expected_context:
        checks["business_context"] = all(context.get(key) == value for key, value in case.expected_context.items())
    if case.expected_stock:
        observed = {item["name"]: item["stock"] for item in context.get("catalog", [])}
        checks["stock_facts"] = all(observed.get(name) == value for name, value in case.expected_stock.items())
    if case.catalog_excludes:
        names = {item["name"] for item in context.get("catalog", [])}
        checks["catalog_access_and_availability"] = not names.intersection(case.catalog_excludes)
    return checks
