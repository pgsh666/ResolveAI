from fastapi import APIRouter, Depends, Query
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.models import Product
from app.schemas import ProductInput, OrderCreate, OrderAction
from app.services.auth import authorize_request, current_actor, require_role
from app.services.store import product_view, validate_image, order_scope, order_view, get_order, create_order, transition, cents
from app.models import StoreOrder

router = APIRouter(dependencies=[Depends(authorize_request)], tags=["store"])


@router.get("/customer/products")
@router.get("/products")
def products(q: str = Query(default="", max_length=120), session: Session = Depends(get_db)):
    actor = current_actor()
    stmt = select(Product).where(Product.workspace_id == actor.workspace_id)
    if actor.role == "customer":
        stmt = stmt.where(Product.active.is_(True))
    if q:
        stmt = stmt.where(Product.name.contains(q, autoescape=True) | Product.description.contains(q, autoescape=True))
    return [product_view(p) for p in session.scalars(stmt.order_by(Product.created_at.desc())).all()]


@router.post("/products", status_code=201)
def add_product(payload: ProductInput, session: Session = Depends(get_db)):
    actor = require_role("admin")
    validate_image(payload.image)
    data = payload.model_dump(exclude={"price"})
    row = Product(**data, price_cents=cents(payload.price), workspace_id=actor.workspace_id)
    session.add(row)
    session.commit()
    return product_view(row)


@router.put("/products/{product_id}")
def edit_product(product_id: str, payload: ProductInput, session: Session = Depends(get_db)):
    from fastapi import HTTPException
    actor = require_role("admin")
    row = session.scalar(select(Product).where(Product.id == product_id, Product.workspace_id == actor.workspace_id).with_for_update())
    if not row:
        raise HTTPException(404, "商品不存在")
    validate_image(payload.image)
    for key, value in payload.model_dump(exclude={"price"}).items():
        setattr(row, key, value)
    row.price_cents = cents(payload.price)
    session.commit()
    return product_view(row)


@router.get("/customer/orders")
def customer_orders(session: Session = Depends(get_db)):
    return [order_view(session, o) for o in session.scalars(select(StoreOrder).where(*order_scope(True)).order_by(StoreOrder.created_at.desc())).all()]


@router.get("/orders")
def merchant_orders(session: Session = Depends(get_db)):
    require_role("admin")
    return [order_view(session, o) for o in session.scalars(select(StoreOrder).where(*order_scope()).order_by(StoreOrder.created_at.desc())).all()]


@router.post("/customer/orders", status_code=201)
def purchase(payload: OrderCreate, session: Session = Depends(get_db)):
    return order_view(session, create_order(session, payload))


@router.post("/customer/orders/{order_id}/action")
def customer_action(order_id: str, payload: OrderAction, session: Session = Depends(get_db)):
    return order_view(session, transition(session, get_order(session, order_id, customer=True), payload, customer=True))


@router.post("/orders/{order_id}/action")
def merchant_action(order_id: str, payload: OrderAction, session: Session = Depends(get_db)):
    require_role("admin")
    if payload.action not in {"ship", "advance", "deliver"}:
        from fastapi import HTTPException
        raise HTTPException(403, "商户仅可发货、推进物流或模拟送达")
    return order_view(session, transition(session, get_order(session, order_id), payload))
