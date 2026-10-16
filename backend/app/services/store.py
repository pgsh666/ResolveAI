"""Persistent simulated shopping. All amounts are integer cents, never real money."""
import base64
import re
from decimal import Decimal, ROUND_HALF_UP
from datetime import timezone

from fastapi import HTTPException
from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError

from app.models import Product, StoreOrder, OrderEvent, PaymentLedger, utc_now
from app.services.auth import current_actor
from app.services.numbering import next_number

STATUS_LABELS = {"pending_payment": "待支付", "paid": "已支付", "in_transit": "未送达",
                 "delivered": "已送达", "signed": "已签收", "cancelled": "已取消"}


def cents(value):
    return int((Decimal(str(value)) * 100).quantize(Decimal("1"), rounding=ROUND_HALF_UP))


def product_view(product):
    return {"id": product.id, "name": product.name, "description": product.description, "image": product.image,
            "price": product.price_cents / 100, "stock": product.stock, "category": product.category,
            "active": product.active, "created_at": product.created_at}


def validate_image(image):
    if not image:
        return
    match = re.fullmatch(r"data:image/(png|jpeg|webp);base64,([A-Za-z0-9+/=]+)", image)
    if not match:
        raise HTTPException(422, "图片请上传 PNG、JPEG 或 WebP 文件")
    try:
        raw = base64.b64decode(match[2], validate=True)
    except ValueError as error:
        raise HTTPException(422, "图片内容无效") from error
    magic = {"png": raw.startswith(b"\x89PNG\r\n\x1a\n"), "jpeg": raw.startswith(b"\xff\xd8\xff"),
             "webp": raw.startswith(b"RIFF") and raw[8:12] == b"WEBP"}
    if len(raw) > 2 * 1024 * 1024 or not magic[match[1]]:
        raise HTTPException(422, "图片格式无效或超过 2MB")


def order_scope(customer=False):
    actor = current_actor()
    conditions = [StoreOrder.workspace_id == actor.workspace_id]
    if customer or actor.role == "customer":
        conditions.append(StoreOrder.owner_id == actor.id)
    return conditions


def get_order(session, identifier, *, customer=False):
    row = session.scalar(select(StoreOrder).where(
        (StoreOrder.id == identifier) | (StoreOrder.order_no == identifier), *order_scope(customer)))
    if not row:
        raise HTTPException(404, "订单不存在")
    return row


def shipping_days(order):
    if not order.shipped_at or order.status != "in_transit":
        return order.transit_days
    at = order.shipped_at.replace(tzinfo=timezone.utc) if order.shipped_at.tzinfo is None else order.shipped_at
    return max(order.transit_days, max(0, (utc_now() - at).days))


def order_view(session, row):
    events = session.scalars(select(OrderEvent).where(OrderEvent.order_id == row.id).order_by(OrderEvent.created_at, OrderEvent.id)).all()
    ledger = session.scalars(select(PaymentLedger).where(PaymentLedger.order_id == row.id).order_by(PaymentLedger.created_at)).all()
    return {"id": row.id, "order_no": row.order_no, "customer_name": row.customer_name,
            "product_name": row.product_name, "quantity": row.quantity, "unit_price": row.unit_price_cents / 100,
            "total": row.total_cents / 100, "refunded": row.refunded_cents / 100,
            "status": row.status, "status_label": STATUS_LABELS[row.status], "address": row.address,
            "expected_delivery_days": row.expected_delivery_days, "transit_days": shipping_days(row),
            "overdue": row.status == "in_transit" and shipping_days(row) > row.expected_delivery_days,
            "created_at": row.created_at, "events": [{"id": e.id, "status": e.status, "description": e.description, "created_at": e.created_at} for e in events],
            "ledger": [{"id": e.id, "kind": e.kind, "amount": e.amount_cents / 100, "mode": "simulated", "created_at": e.created_at} for e in ledger]}


def create_order(session, payload):
    actor = current_actor()
    key = f"{actor.id}:{payload.idempotency_key}"
    old = session.scalar(select(StoreOrder).where(StoreOrder.request_key == key))
    if old:
        if (old.product_id, old.quantity, old.address) != (payload.product_id, payload.quantity, payload.address.strip()):
            raise HTTPException(409, "相同下单键不能用于不同订单")
        return old
    product = session.scalar(select(Product).where(Product.id == payload.product_id, Product.workspace_id == actor.workspace_id))
    if not product:
        raise HTTPException(404, "商品不存在")
    result = session.execute(update(Product).where(Product.id == product.id, Product.active.is_(True),
                            Product.stock >= payload.quantity).values(stock=Product.stock - payload.quantity))
    if result.rowcount != 1:
        session.rollback()
        raise HTTPException(409, "商品已下架或库存不足")
    order = StoreOrder(order_no=next_number(session, "order"), request_key=key, owner_id=actor.id,
                       workspace_id=actor.workspace_id, customer_name=actor.display_name,
                       product_id=product.id, product_name=product.name, quantity=payload.quantity,
                       unit_price_cents=product.price_cents, total_cents=product.price_cents * payload.quantity,
                       address=payload.address.strip(), status="pending_payment")
    session.add(order)
    session.flush()
    session.add(OrderEvent(order_id=order.id, status=order.status, description="已下单，库存已预留，等待模拟支付"))
    try:
        session.commit()
    except IntegrityError:
        session.rollback()
        old = session.scalar(select(StoreOrder).where(StoreOrder.request_key == key))
        if old:
            return old
        raise
    return order


def transition(session, order, payload, *, customer=False):
    action = payload.action
    if customer and action not in {"pay", "cancel", "sign"}:
        raise HTTPException(403, "客户只能支付、取消未付款订单或确认签收")
    allowed = {"pay": ("pending_payment", "paid"), "cancel": ("pending_payment", "cancelled"),
               "ship": ("paid", "in_transit"), "advance": ("in_transit", "in_transit"),
               "deliver": ("in_transit", "delivered"), "sign": ("delivered", "signed")}
    before, after = allowed[action]
    if order.status == after and action in {"pay", "sign", "cancel", "ship", "deliver"}:
        return order
    if order.status != before:
        raise HTTPException(409, "订单当前状态不允许此操作")
    values = {"status": after, "updated_at": utc_now()}
    if action == "ship":
        values.update(shipped_at=utc_now(), expected_delivery_days=payload.expected_delivery_days or 3)
    if action in {"advance", "deliver"}:
        days = payload.transit_days if payload.transit_days is not None else shipping_days(order) + (action == "advance")
        if days < shipping_days(order):
            raise HTTPException(422, "模拟运输天数不能倒退")
        values["transit_days"] = days
    result = session.execute(update(StoreOrder).where(StoreOrder.id == order.id, StoreOrder.status == before,
                            StoreOrder.transit_days == order.transit_days).values(**values))
    if result.rowcount != 1:
        session.rollback()
        raise HTTPException(409, "订单刚刚发生变化，请刷新后重试")
    if action == "pay":
        session.add(PaymentLedger(order_id=order.id, kind="payment", amount_cents=order.total_cents, idempotency_key=f"pay:{order.id}"))
    if action == "cancel":
        session.execute(update(Product).where(Product.id == order.product_id).values(stock=Product.stock + order.quantity))
    descriptions = {"pay": "模拟支付成功，未发生真实扣款", "cancel": "未支付订单已取消，预留库存已释放",
                    "ship": "商户已模拟发货，包裹运输中", "advance": "已推进模拟运输时间", "deliver": "包裹已模拟送达，等待客户签收", "sign": "客户已确认签收"}
    session.add(OrderEvent(order_id=order.id, status=after, description=descriptions[action]))
    session.commit()
    session.refresh(order)
    return order


def record_refund(session, order_no, amount, key):
    order = session.scalar(select(StoreOrder).where(StoreOrder.order_no == order_no))
    if not order:
        return  # Legacy demonstration tools may have no storefront order.
    if session.scalar(select(PaymentLedger).where(PaymentLedger.idempotency_key == key)):
        return
    value = cents(amount)
    result = session.execute(update(StoreOrder).where(StoreOrder.id == order.id,
        StoreOrder.status.in_(["paid", "in_transit", "delivered", "signed"]),
        StoreOrder.total_cents - StoreOrder.refunded_cents >= value).values(refunded_cents=StoreOrder.refunded_cents + value))
    if value <= 0 or result.rowcount != 1:
        raise ValueError("模拟退款超过订单可退金额或订单未支付")
    session.add(PaymentLedger(order_id=order.id, kind="refund", amount_cents=value, idempotency_key=key))
    session.add(OrderEvent(order_id=order.id, status=order.status, description=f"人工审核后模拟退款 {value / 100:.2f} 元，未发生真实资金流转"))
