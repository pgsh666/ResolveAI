import os
import tempfile
from pathlib import Path

import pytest


TEST_DB = Path(tempfile.mkdtemp(prefix="resolveai-tests-")) / "test_resolveai.db"
os.environ["APP_ENV"] = "test"
os.environ["AUTH_ENABLED"] = "false"
os.environ["AGENT_MODE"] = "demo"
os.environ["DATABASE_URL"] = f"sqlite:///{TEST_DB.as_posix()}"
os.environ["MCP_ENABLED"] = "false"
os.environ["EMBEDDING_PROVIDER"] = "local"


@pytest.fixture(scope="session", autouse=True)
def clean_test_database():
    if TEST_DB.exists():
        TEST_DB.unlink()
    # These synthetic products exist only in pytest's temporary database, not
    # in normal application startup or any published business data.
    from app.core.database import init_db, SessionLocal
    from app.models import Product
    init_db()
    with SessionLocal() as session:
        for name, description, price, stock in [
            ("轻便蓝牙耳机", "入耳式蓝牙耳机，适合通勤，续航约 6 小时。", 12900, 30),
            ("降噪蓝牙耳机", "头戴式蓝牙耳机，支持主动降噪，续航约 20 小时。", 29900, 20),
            ("无线鼠标", "办公用无线鼠标，轻巧便携。", 5900, 50),
            ("机械键盘", "87 键机械键盘，适用于办公和游戏。", 19900, 25),
            ("保温水杯", "500ml 保温水杯，食品接触级内胆。", 7900, 40),
        ]:
            session.add(Product(name=name, description=description, price_cents=price, stock=stock, category="数码", active=True))
        session.commit()
    yield
    from app.core.database import engine
    engine.dispose()
    if TEST_DB.exists():
        TEST_DB.unlink()


@pytest.fixture
def paid_order():
    """Create a real persistent simulated order instead of inventing an order number."""
    from uuid import uuid4
    def make(client, price=299):
        product = client.post("/api/v1/products", json={"name": "测试商品" + uuid4().hex[:6], "description": "用于真实模拟商城订单的测试商品", "price": price, "stock": 10}).json()
        created = client.post("/api/v1/customer/orders", json={"product_id": product["id"], "quantity": 1, "address": "测试收货地址", "idempotency_key": uuid4().hex})
        assert created.status_code == 201, created.text
        order = created.json()
        response = client.post(f"/api/v1/customer/orders/{order['id']}/action", json={"action": "pay"})
        assert response.status_code == 200, response.text
        return response.json()
    return make
