import pytest
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import Session

from app.core.database import Base
from app.models import Ticket, Product, StoreOrder, PaymentLedger, QuestionRoute, ModelUsage, Account, KnowledgeDocument
from app.services.seed import seed_database


@pytest.mark.parametrize("starts", [1, 2])
def test_public_initialization_has_knowledge_but_no_example_business_data(starts):
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    with Session(engine) as session:
        for _ in range(starts):
            seed_database(session)
        for model in (Ticket, Product, StoreOrder, PaymentLedger, QuestionRoute, ModelUsage, Account):
            assert session.scalar(select(func.count()).select_from(model)) == 0
        assert session.scalar(select(func.count()).select_from(KnowledgeDocument)) == 21
    engine.dispose()


def test_public_initialization_does_not_delete_user_created_records():
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    with Session(engine) as session:
        product = Product(name="商户自行录入商品", description="由商户维护的资料", price_cents=1999, stock=2)
        session.add(product)
        session.commit()
        product_id = product.id
        seed_database(session)
        session.refresh(product)
        assert product.id == product_id and product.name == "商户自行录入商品"
        assert session.scalar(select(func.count()).select_from(Product)) == 1
        assert session.scalar(select(func.count()).select_from(Ticket)) == 0
    engine.dispose()
