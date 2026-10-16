"""Run only against a disposable PostgreSQL container, never a business database."""
import os
from datetime import datetime, timezone
from concurrent.futures import ThreadPoolExecutor
from uuid import uuid4

os.environ.update(APP_ENV="test", AUTH_ENABLED="false", AGENT_MODE="demo", EMBEDDING_PROVIDER="local",
                  MCP_ENABLED="false", ASYNC_TASKS_ENABLED="false", OTEL_ENABLED="false")
from sqlalchemy.engine import make_url
address = make_url(os.environ["DATABASE_URL"])
if address.host not in {"localhost", "127.0.0.1"} or address.port != 15433 or address.database != "postgres":
    raise SystemExit("Refusing to test outside the isolated PostgreSQL container on port 15433")

from fastapi.testclient import TestClient
from sqlalchemy.orm import Session
from sqlalchemy import inspect
from app.main import app
from app.core.database import engine
from app.services.numbering import next_number


def verify():
    with TestClient(app) as client:
        assert "question_routes" in inspect(engine).get_table_names()
        at = datetime(2098, 12, 31, 0, tzinfo=timezone.utc)
        def allocate(_):
            with Session(engine) as session:
                number = next_number(session, at=at)
                session.commit()
                return number
        with ThreadPoolExecutor(max_workers=8) as pool:
            numbers = list(pool.map(allocate, range(20)))
        assert set(numbers) == {f"98-12-31-{i}" for i in range(1, 21)}
        product = client.post("/api/v1/products", json={"name":"PostgreSQL 验证商品", "description":"用于隔离数据库验证库存和支付", "price":39.99,"stock":1}).json()
        body = {"product_id":product["id"], "quantity":1, "address":"测试地址", "idempotency_key":uuid4().hex}
        r = client.post("/api/v1/customer/orders",json=body)
        assert r.status_code == 201, r.text
        order = r.json()
        assert client.post("/api/v1/customer/orders",json=body).json()["id"] == order["id"]
        assert client.post("/api/v1/customer/orders",json={**body,"idempotency_key":uuid4().hex}).status_code == 409
        path = f"/api/v1/customer/orders/{order['id']}/action"
        assert client.post(path,json={"action":"pay"}).json()["status"] == "paid"
        assert len(client.post(path,json={"action":"pay"}).json()["ledger"]) == 1
        merchant = f"/api/v1/orders/{order['id']}/action"
        client.post(merchant,json={"action":"ship"})
        question = client.post("/api/v1/customer/tickets",json={"customer_name":"测试客户", "subject":"查询物流", "message":"请查物流状态", "order_id":order["order_no"]}).json()
        assert not question["handed_off"] and question["messages"][-1]["role"] == "support"
        client.post(merchant,json={"action":"advance","transit_days":4})
        late = client.post("/api/v1/customer/tickets",json={"customer_name":"测试客户", "subject":"查询物流", "message":"请查物流状态", "order_id":order["order_no"]}).json()
        assert late["handed_off"]
        rows = client.get("/api/v1/questions/routes").json()
        assert len(rows) == 2 and all(row["steps"] for row in rows)
        client.post(merchant,json={"action":"deliver"})
        assert client.post(path,json={"action":"sign"}).json()["status"] == "signed"
    print("PostgreSQL: migrations, concurrent numbering, inventory, payment idempotency, auto reply, handoff, question traces and signing passed")


if __name__ == "__main__":
    verify()
