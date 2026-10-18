import json
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from app.main import app
from app.core.database import Base, SessionLocal
from app.models import Ticket, QuestionRoute
from app.services.llm import LLMService
from app.services.numbering import next_number
from app.services.retrieval import RetrievalService


def question(client, text, order=None, amount=None):
    response = client.post("/api/v1/customer/tickets", json={"customer_name": "购物客户", "subject": "购物与商品咨询", "message": text, "order_id": order, "amount": amount})
    assert response.status_code == 201, response.text
    return response.json()


def test_daily_numbers_are_atomic_and_use_china_date(tmp_path):
    engine = create_engine(f"sqlite:///{(tmp_path / 'numbers.db').as_posix()}", connect_args={"timeout": 30})
    Base.metadata.create_all(engine)
    at = datetime(2026, 10, 13, 16, 1, tzinfo=timezone.utc)
    def allocate(_):
        with Session(engine) as session:
            number = next_number(session, at=at)
            session.commit()
            return number
    with ThreadPoolExecutor(max_workers=8) as pool:
        numbers = list(pool.map(allocate, range(20)))
    assert len(set(numbers)) == 20
    assert set(numbers) == {f"26-10-14-{i}" for i in range(1, 21)}
    with Session(engine) as session:
        assert next_number(session, at=datetime(2026, 10, 14, 16, tzinfo=timezone.utc)) == "26-10-15-1"
    engine.dispose()


def test_store_full_lifecycle_stock_price_and_payment_idempotency():
    with TestClient(app) as client:
        p = client.post("/api/v1/products", json={"name": "流程验证商品", "description": "测试价格快照与库存预留", "price": 19.99, "stock": 2}).json()
        payload = {"product_id": p["id"], "quantity": 2, "address": "模拟收货地址", "idempotency_key": uuid4().hex}
        response = client.post("/api/v1/customer/orders", json=payload)
        assert response.status_code == 201
        o = response.json(); path = f"/api/v1/customer/orders/{o['id']}/action"
        assert o["total"] == 39.98 and o["status"] == "pending_payment"
        assert client.post("/api/v1/customer/orders", json=payload).json()["id"] == o["id"]
        exhausted = client.post("/api/v1/customer/orders", json={**payload, "idempotency_key": uuid4().hex})
        assert exhausted.status_code == 409
        assert client.post(path, json={"action": "sign"}).status_code == 409
        assert client.post(path, json={"action": "ship"}).status_code == 403
        client.put(f"/api/v1/products/{p['id']}", json={"name": p["name"], "description": p["description"], "price": 99, "stock": 0})
        paid = client.post(path, json={"action": "pay"}).json()
        again = client.post(path, json={"action": "pay"}).json()
        assert paid["total"] == 39.98 and len(again["ledger"]) == 1
        merchant = f"/api/v1/orders/{o['id']}/action"
        assert client.post(merchant, json={"action": "ship"}).json()["status"] == "in_transit"
        assert client.post(merchant, json={"action": "deliver"}).json()["status"] == "delivered"
        signed = client.post(path, json={"action": "sign"}).json()
        assert signed["status"] == "signed" and len(signed["events"]) == 5
        assert any(x["id"] == o["id"] for x in client.get("/api/v1/orders").json())


def test_cancel_releases_stock_and_hidden_products_cannot_be_bought():
    with TestClient(app) as client:
        p = client.post("/api/v1/products", json={"name":"预留库存", "description":"用于取消订单测试", "price":49, "stock":1}).json()
        o = client.post("/api/v1/customer/orders", json={"product_id":p["id"], "quantity":1, "address":"测试地址", "idempotency_key":uuid4().hex}).json()
        assert client.post(f"/api/v1/customer/orders/{o['id']}/action", json={"action":"cancel"}).json()["status"] == "cancelled"
        restored = next(x for x in client.get("/api/v1/products").json() if x["id"] == p["id"])
        assert restored["stock"] == 1
        client.put(f"/api/v1/products/{p['id']}", json={"name":p["name"], "description":p["description"], "price":49, "stock":1, "active":False})
        assert client.post("/api/v1/customer/orders", json={"product_id":p["id"], "quantity":1, "address":"测试地址", "idempotency_key":uuid4().hex}).status_code == 409


def test_catalog_auto_reply_multiturn_memory_and_per_question_trace(monkeypatch):
    seen = []
    original = LLMService.resolve
    def capture(self, ticket, context, citations, policy):
        seen.append(ticket)
        return original(self, ticket, context, citations, policy)
    monkeypatch.setattr(LLMService, "resolve", capture)
    with TestClient(app) as client:
        first = question(client, "有没有蓝牙耳机，预算 300 元？")
        assert first["stage"] == "answered" and not first["handed_off"]
        assert "129.00" in first["messages"][-1]["content"]
        follow = client.post(f"/api/v1/customer/tickets/{first['id']}/messages", json={"message":"这两个哪个更便宜？"})
        assert follow.status_code == 200, follow.text
        assert "轻便蓝牙耳机" in follow.json()["messages"][-1]["content"]
        assert len(seen[-1]["conversation"]) == 3
        assert seen[-1]["memory"]["original_question"] == "有没有蓝牙耳机，预算 300 元？"
        rows = [r for r in client.get("/api/v1/questions/routes").json() if r["ticket_id"] == first["id"]]
        assert len(rows) == 2 and len({r["message_id"] for r in rows}) == 2
        assert all(r["outcome"] == "replied" and r["steps"] and r["total_ms"] > 0 and r["api_requests"] == 0 for r in rows)
        assert client.get(f"/api/v1/tickets/{first['id']}").json()["status"] == "awaiting_customer"
    # A new server session loads the same persisted dialogue.
    with TestClient(app) as client:
        restored = client.get(f"/api/v1/customer/tickets/{first['id']}").json()
        assert len(restored["messages"]) == 4


@pytest.mark.parametrize("reason", ["请转人工客服", "我要退款49元"])
def test_explicit_handoff_and_small_refunds_never_autowrite(reason, paid_order):
    with TestClient(app) as client:
        order = paid_order(client, 49)
        t = question(client, reason, order["order_no"], 49)
        assert t["handed_off"] and t["handoff_reasons"]
        d = client.get(f"/api/v1/tickets/{t['id']}").json()
        assert d["status"] == "pending_approval"
        attempt = client.post(f"/api/v1/tickets/{t['id']}/tools/execute_refund/execute", json={"idempotency_key":uuid4().hex, "arguments":{"order_id":order["order_no"],"amount":49,"currency":"CNY","reason":"未审批测试"}})
        assert attempt.status_code == 403
        more = client.post(f"/api/v1/customer/tickets/{t['id']}/messages", json={"message":"补充信息，请继续处理"}).json()
        assert more["handed_off"] and more["messages"][-1]["role"] == "customer"
        log = next(r for r in client.get("/api/v1/questions/routes").json() if r["message_id"] == more["messages"][-1]["id"])
        assert log["outcome"] == "waiting_human" and log["api_requests"] == 0


def test_long_conversation_continues_and_overdue_logistics_still_escalates(paid_order):
    with TestClient(app) as client:
        t = question(client, "商城有没有蓝牙耳机？")
        for _ in range(9):
            t = client.post(f"/api/v1/customer/tickets/{t['id']}/messages", json={"message":"这两个价格比较一下"}).json()
            assert not t["handed_off"]
        assert sum(m["role"] == "customer" for m in t["messages"]) == 10
        assert t["handoff_reasons"] == []
        human = client.post(f"/api/v1/customer/tickets/{t['id']}/messages", json={"message":"请转人工客服"}).json()
        assert human["handed_off"] and "用户要求转人工" in human["handoff_reasons"]
        o = paid_order(client)
        client.post(f"/api/v1/orders/{o['id']}/action", json={"action":"ship", "expected_delivery_days":3})
        client.post(f"/api/v1/orders/{o['id']}/action", json={"action":"advance", "transit_days":3})
        normal = question(client, "请查物流状态", o["order_no"])
        assert not normal["handed_off"]
        client.post(f"/api/v1/orders/{o['id']}/action", json={"action":"advance", "transit_days":4})
        late = question(client, "请查物流状态", o["order_no"])
        assert late["handed_off"] and "运输天数超过预计送达天数" in late["handoff_reasons"]


def test_manual_faq_disable_enable_changes_retrieval():
    with TestClient(app) as client:
        title = "测试独有星光充电政策"
        d = client.post("/api/v1/knowledge/documents", json={"title":title, "source":f"faq://{uuid4().hex}", "content":"测试独有星光充电政策规定充电期间保持接口干燥。", "category":"product"}).json()
        def results(): return client.get("/api/v1/knowledge/search", params={"q":title}).json()["results"]
        assert any(c["id"] == d["id"] for c in results())
        assert client.patch(f"/api/v1/knowledge/documents/{d['id']}", json={"enabled":False}).json()["enabled"] is False
        assert all(c["id"] != d["id"] for c in results())
        client.patch(f"/api/v1/knowledge/documents/{d['id']}", json={"enabled":True})
        assert any(c["id"] == d["id"] for c in results())


def test_failed_model_path_preserves_question_and_routes_to_human(monkeypatch):
    from app.services.workflow import WorkflowService
    async def broken(*args, **kwargs): raise RuntimeError("模拟工作流故障")
    monkeypatch.setattr(WorkflowService, "analyze", broken)
    with TestClient(app) as client:
        t = question(client, "查询蓝牙耳机")
        assert t["handed_off"] and t["messages"][-1]["role"] == "support"
        row = next(r for r in client.get("/api/v1/questions/routes").json() if r["ticket_id"] == t["id"])
        assert row["outcome"] == "failed_to_human" and row["degraded"] and row["error"]
    with SessionLocal() as session:
        assert session.scalar(select(QuestionRoute).where(QuestionRoute.ticket_id == t["id"])) is not None
        assert session.get(Ticket, t["id"]).message == "查询蓝牙耳机"


def test_prompt_bound_keeps_latest_turn_and_structured_facts():
    service = LLMService()
    payload = {"user_input_untrusted":{"message":"最新问题", "conversation":[{"content":f"第{i}轮"} for i in range(30)]}, "trusted_tool_context":{"order_id":"ORD-26-10-14-1"}}
    bounded = json.loads(service._bounded_json(payload))
    assert bounded["user_input_untrusted"]["conversation"][-1]["content"] == "第29轮"
    assert bounded["trusted_tool_context"]["order_id"] == "ORD-26-10-14-1"


def test_question_api_counts_include_failed_model_attempts_without_paid_calls(monkeypatch):
    original_init = LLMService.__init__
    triage_attempts = 0
    def init_fake(self, *args, **kwargs):
        original_init(self, *args, **kwargs)
        self.settings = self.settings.model_copy(update={"agent_mode":"deepseek", "model_max_retries":1, "retry_backoff_seconds":0})
        self.client = object()
    def parse_fake(self, schema, system_prompt, user_prompt):
        nonlocal triage_attempts
        if self._current_stage == "triage":
            triage_attempts += 1
            if triage_attempts == 1:
                raise TimeoutError("模拟一次模型超时")
            return schema(category="product", priority="medium", sentiment="neutral", intent="选购商品", confidence=.95)
        payload = json.loads(user_prompt)
        return self._demo_resolution(payload["user_input_untrusted"], payload["trusted_tool_context"], payload["trusted_business_policy"])
    monkeypatch.setattr(LLMService, "__init__", init_fake)
    monkeypatch.setattr(LLMService, "_parse", parse_fake)
    with TestClient(app) as client:
        t = question(client, "商城有没有蓝牙耳机？")
        row = next(r for r in client.get("/api/v1/questions/routes").json() if r["ticket_id"] == t["id"])
        assert row["outcome"] == "replied"
        # Stock facts are now rendered directly; only the two triage attempts call a model.
        assert row["model_requests"] == 2 and row["api_requests"] == 2 and row["tool_requests"] == 0
        detail = client.get(f"/api/v1/tickets/{t['id']}").json()
        resolution = next(item for item in detail["assessment"]["model_trace"] if item["stage"] == "resolution")
        assert resolution["provider"] == "catalog_availability_guard" and resolution["skipped"]


def test_concurrent_followups_do_not_overwrite_dialogue(monkeypatch):
    import asyncio
    from threading import Barrier
    from app.services.workflow import WorkflowService
    original = WorkflowService.analyze
    async def delayed(self, ticket, *args, **kwargs):
        await asyncio.sleep(.15)
        return await original(self, ticket, *args, **kwargs)
    with TestClient(app) as client:
        t = question(client, "商城有没有蓝牙耳机？")
        monkeypatch.setattr(WorkflowService, "analyze", delayed)
        barrier = Barrier(2)
        def send(text):
            barrier.wait()
            return client.post(f"/api/v1/customer/tickets/{t['id']}/messages", json={"message":text})
        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(send, ["比较这两个的价格", "这两个哪个便宜？"]))
        assert sorted(r.status_code for r in results) == [200, 409]
        saved = client.get(f"/api/v1/customer/tickets/{t['id']}").json()
        assert sum(m["role"] == "customer" for m in saved["messages"]) == 2
        rows = [r for r in client.get("/api/v1/questions/routes").json() if r["ticket_id"] == t["id"]]
        assert len(rows) == 2


def test_chat_only_message_supports_commerce_service_and_natural_order_context(paid_order):
    with TestClient(app) as client:
        first = client.post("/api/v1/customer/tickets", json={"message":"您好"})
        assert first.status_code == 201, first.text
        t = first.json()
        assert not t["handed_off"] and "订单编号" not in t["sent_response"]
        follow = client.post(f"/api/v1/customer/tickets/{t['id']}/messages", json={"message":"您可以帮我做什么？"}).json()
        assert not follow["handed_off"] and "比较价格" in follow["sent_response"]
        assert "学习" not in follow["sent_response"]
        assert "请您提供订单编号" not in follow["sent_response"]
        row = next(r for r in client.get("/api/v1/questions/routes").json() if r["ticket_id"] == t["id"])
        assert row["route"] == "service_scope" and row["outcome"] == "replied" and row["api_requests"] == 0
        client.post("/api/v1/products", json={"name":"聊天验证蓝牙耳机", "description":"用于无需订单的选购咨询", "price":59, "stock":3})
        shopping = client.post(f"/api/v1/customer/tickets/{t['id']}/messages", json={"message":"商城有没有蓝牙耳机？"}).json()
        assert not shopping["handed_off"] and "聊天验证蓝牙耳机" in shopping["sent_response"]
        assert "请您提供订单编号" not in shopping["sent_response"]
        order = paid_order(client, 99)
        refund = client.post("/api/v1/customer/tickets", json={"message":f"订单号{order['order_no']}，我申请退款49元"}).json()
        detail = client.get(f"/api/v1/tickets/{refund['id']}").json()
        assert detail["order_id"] == order["order_no"] and detail["amount"] == 49
        assert refund["handed_off"] and detail["approval"]["amount"] == 49


@pytest.mark.parametrize("message", ["帮我规划学习Python的步骤", "推荐周末的旅游路线", "帮我写一篇作文"])
def test_noncommerce_requests_only_receive_service_scope_guidance(monkeypatch, message):
    def unexpected_model_answer(*args, **kwargs):
        raise AssertionError("范围外问题不得调用自由生成回复")
    monkeypatch.setattr(LLMService, "resolve", unexpected_model_answer)
    with TestClient(app) as client:
        response = client.post("/api/v1/customer/tickets", json={"message":message})
        assert response.status_code == 201, response.text
        t = response.json()
        assert not t["handed_off"] and "电商客服服务" in t["sent_response"]
        assert "通用问答" in t["sent_response"] and "基础语法" not in t["sent_response"]
        row = next(r for r in client.get("/api/v1/questions/routes").json() if r["ticket_id"] == t["id"])
        assert row["route"] == "service_scope" and row["outcome"] == "replied" and row["api_requests"] == 0


@pytest.mark.parametrize("category", ["product", "other"])
@pytest.mark.parametrize("message", ["帮我找一款蓝牙耳机", "帮我挑一款蓝牙耳机", "看看蓝牙耳机有哪些", "蓝牙耳机有货吗"])
def test_catalog_search_is_not_blocked_by_missing_policy_documents(monkeypatch, category, message):
    original_triage = LLMService._demo_triage
    monkeypatch.setattr(LLMService, "_demo_triage", staticmethod(
        lambda subject, text: original_triage(subject, text).model_copy(update={"category":category})))
    monkeypatch.setattr(RetrievalService, "search_reliable", lambda *args, **kwargs: ([], {
        "reliable":False, "threshold":.55, "top_score":.4726,
        "failure_reason":"最高相似度低于可靠性阈值", "candidate_count":3, "reliable_count":0,
    }))
    with TestClient(app) as client:
        response = client.post("/api/v1/customer/tickets", json={"message":message})
        assert response.status_code == 201, response.text
        t = response.json()
        assert not t["handed_off"], t["sent_response"]
        assert "轻便蓝牙耳机" in t["sent_response"] and "降噪蓝牙耳机" in t["sent_response"]
        detail = client.get(f"/api/v1/tickets/{t['id']}").json()
        assert detail["assessment"]["customer_context"]["assistant_route"] == "catalog"
        assert detail["assessment"]["resilience"]["rag"]["reliable"] is False
        assert detail["assessment"]["quality"]["passed"] is True
        row = next(r for r in client.get("/api/v1/questions/routes").json() if r["ticket_id"] == t["id"])
        assert row["route"] == "catalog" and row["outcome"] == "replied"


def test_unknown_catalog_product_is_answered_without_inventing_stock_or_handoff(monkeypatch):
    monkeypatch.setattr(RetrievalService, "search_reliable", lambda *args, **kwargs: ([], {
        "reliable":False, "failure_reason":"没有政策依据",
    }))
    with TestClient(app) as client:
        response = client.post("/api/v1/customer/tickets", json={"message":"帮我找一款书包"})
        assert response.status_code == 201, response.text
        t = response.json()
        assert not t["handed_off"] and "没有找到" in t["sent_response"]
        row = next(r for r in client.get("/api/v1/questions/routes").json() if r["ticket_id"] == t["id"])
        assert row["route"] == "catalog" and row["outcome"] == "replied"


@pytest.mark.parametrize("category", ["product", "other"])
@pytest.mark.parametrize("message", ["什么时候上架新的", "新的什么时候上架", "什么时候补货", "啥时候有新款键盘", "新的什么时候有"])
def test_catalog_followup_keeps_latest_topic_and_reports_unknown_schedule(monkeypatch, category, message):
    original_triage = LLMService._demo_triage
    monkeypatch.setattr(LLMService, "_demo_triage", staticmethod(
        lambda subject, text: original_triage(subject, text).model_copy(update={"category":category})))
    monkeypatch.setattr(RetrievalService, "search_reliable", lambda *args, **kwargs: ([], {
        "reliable":False, "threshold":.55, "top_score":.5159, "failure_reason":"最高相似度低于可靠性阈值",
    }))
    original_resolve = LLMService.resolve
    def resolve_only_recorded_facts(self, ticket, *args, **kwargs):
        assert ticket["message"] != message, "未知上新计划不得交给模型自由生成时间"
        return original_resolve(self, ticket, *args, **kwargs)
    monkeypatch.setattr(LLMService, "resolve", resolve_only_recorded_facts)
    with TestClient(app) as client:
        first = client.post("/api/v1/customer/tickets", json={"message":"比较商城商品价格"}).json()
        assert not first["handed_off"]
        second = client.post(f"/api/v1/customer/tickets/{first['id']}/messages", json={"message":"有没有更便宜的键盘"}).json()
        assert not second["handed_off"] and "机械键盘" in second["sent_response"]
        third = client.post(f"/api/v1/customer/tickets/{first['id']}/messages", json={"message":message}).json()
        assert not third["handed_off"], third["sent_response"]
        assert "键盘" in third["sent_response"] and "暂时无法确认具体时间" in third["sent_response"]
        detail = client.get(f"/api/v1/tickets/{first['id']}").json()["assessment"]
        context = detail["customer_context"]
        assert context["assistant_route"] == "catalog" and context["catalog_topic"] == "键盘"
        assert context["catalog_intent"] == "availability_plan" and context["availability_plan_available"] is False
        assert all("键盘" in p["name"] for p in context["catalog"])
        assert detail["quality"]["passed"] and not detail["resilience"]["escalated_to_human"]
        latest_row = next(r for r in client.get("/api/v1/questions/routes").json() if r["question"] == message and r["ticket_id"] == first["id"])
        assert latest_row["route"] == "catalog" and latest_row["outcome"] == "replied"


@pytest.mark.parametrize("message", ["还有别的吗", "再推荐一个", "多少钱", "有货吗", "这款怎么样"])
def test_short_catalog_followups_only_use_latest_product_topic(message):
    with TestClient(app) as client:
        first = client.post("/api/v1/customer/tickets", json={"message":"帮我找一款蓝牙耳机"}).json()
        second = client.post(f"/api/v1/customer/tickets/{first['id']}/messages", json={"message":"换成键盘看看"}).json()
        assert not second["handed_off"] and "机械键盘" in second["sent_response"]
        third = client.post(f"/api/v1/customer/tickets/{first['id']}/messages", json={"message":message}).json()
        assert not third["handed_off"], third["sent_response"]
        context = client.get(f"/api/v1/tickets/{first['id']}").json()["assessment"]["customer_context"]
        assert context["assistant_route"] == "catalog" and context["catalog_topic"] == "键盘"
        assert all("键盘" in p["name"] for p in context["catalog"])


@pytest.mark.parametrize("message", ["帮我规划学习Python", "再帮我推荐旅游路线"])
def test_catalog_context_does_not_turn_out_of_scope_requests_into_shopping(message):
    with TestClient(app) as client:
        first = client.post("/api/v1/customer/tickets", json={"message":"帮我找一款蓝牙耳机"}).json()
        follow = client.post(f"/api/v1/customer/tickets/{first['id']}/messages", json={"message":message}).json()
        assert not follow["handed_off"] and "电商客服服务" in follow["sent_response"]
        context = client.get(f"/api/v1/tickets/{first['id']}").json()["assessment"]["customer_context"]
        assert context["assistant_route"] == "service_scope"


@pytest.mark.parametrize("message", ["我要退款", "请转人工客服"])
def test_unknown_catalog_schedule_does_not_bypass_human_boundaries(message):
    with TestClient(app) as client:
        first = client.post("/api/v1/customer/tickets", json={"message":"帮我找键盘"}).json()
        second = client.post(f"/api/v1/customer/tickets/{first['id']}/messages", json={"message":"什么时候上架新的"}).json()
        assert not second["handed_off"]
        third = client.post(f"/api/v1/customer/tickets/{first['id']}/messages", json={"message":message}).json()
        assert third["handed_off"] and third["handoff_reasons"]


def test_repeated_unknown_catalog_schedule_does_not_handoff_by_turn_count():
    with TestClient(app) as client:
        first = client.post("/api/v1/customer/tickets", json={"message":"帮我找键盘"}).json()
        for _ in range(2, 11):
            reply = client.post(f"/api/v1/customer/tickets/{first['id']}/messages", json={"message":"什么时候上架新的"}).json()
            assert not reply["handed_off"] and reply["handoff_reasons"] == []
