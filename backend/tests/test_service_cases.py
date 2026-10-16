from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.core.database import SessionLocal
from app.main import app
from app.models import Ticket
from app.services.service_cases import conversation_case_type


@pytest.mark.parametrize("message", [
    "帮我找一款蓝牙耳机", "有没有更便宜的键盘", "什么时候上架新的",
    "查看我的订单物流", "转人工客服", "有没有优惠券", "如何注册账号",
    "退货政策是什么", "申请退款需要满足哪些条件", "是否支持七天无理由退货",
    "如果收到的商品坏了应该怎么办", "如果我想退货需要什么条件",
    "退款通常多久到账", "售后政策是什么",
])
def test_shopping_and_policy_questions_are_not_after_sales(message):
    ticket = SimpleNamespace(subject="普通咨询", message=message, assessment={
        "conversation":[{"role":"customer", "content":message},
                        {"role":"support", "content":"必要时可以申请退款或联系售后"}],
        "handed_off":True, "category":"refund",
    })
    assert conversation_case_type(ticket) == "consultation"


@pytest.mark.parametrize("message", [
    "我要退款", "申请退货", "请帮我换货", "我的耳机坏了，退货政策是什么",
    "收到的杯子破损了", "订单被重复扣款", "付款成功了但没有下单成功",
    "物流一直超时", "我的退款没有到账", "换了一次的货还是坏的", "签收有争议",
])
def test_actual_customer_issues_are_after_sales(message):
    ticket = SimpleNamespace(subject="购物与商品咨询", message="帮我找耳机", assessment={
        "conversation":[{"role":"customer", "content":"帮我找蓝牙耳机"},
                        {"role":"support", "content":"有耳机在售"},
                        {"role":"customer", "content":message}],
    })
    assert conversation_case_type(ticket) == "after_sales"


def test_overdue_and_legacy_records_classify_without_rewriting_history():
    overdue = SimpleNamespace(subject="物流查询", message="查一下物流", assessment={"customer_context":{"overdue":True}})
    legacy = SimpleNamespace(subject="退款与售后", message="商品破损，请退款", assessment=None)
    product = SimpleNamespace(subject="退款与售后", message="帮我找耳机", assessment=None)
    assert conversation_case_type(overdue) == conversation_case_type(legacy) == "after_sales"
    assert conversation_case_type(product) == "consultation"


def test_listing_separates_all_chats_after_sales_and_ordinary_handoff():
    with TestClient(app) as client:
        created = client.post("/api/v1/customer/tickets", json={"message":"帮我找一款蓝牙耳机"})
        assert created.status_code == 201, created.text
        ticket_id = created.json()["id"]
        original_no = created.json()["ticket_no"]
        all_chats = client.get("/api/v1/tickets").json()
        assert next(t for t in all_chats if t["id"] == ticket_id)["case_type"] == "consultation"
        assert ticket_id in {t["id"] for t in client.get("/api/v1/tickets?case_type=consultation").json()}
        assert ticket_id not in {t["id"] for t in client.get("/api/v1/tickets?case_type=after_sales").json()}
        human = client.post(f"/api/v1/customer/tickets/{ticket_id}/messages", json={"message":"请转人工客服"})
        assert human.status_code == 200 and human.json()["handed_off"] is True
        assert ticket_id in {t["id"] for t in client.get("/api/v1/tickets?status=pending_approval").json()}
        assert ticket_id not in {t["id"] for t in client.get("/api/v1/tickets?case_type=after_sales").json()}
        complaint = client.post(f"/api/v1/customer/tickets/{ticket_id}/messages", json={"message":"之前买的耳机坏了，我要申请退款"})
        assert complaint.status_code == 200
        after_sales = client.get("/api/v1/tickets?case_type=after_sales").json()
        updated = next(t for t in after_sales if t["id"] == ticket_id)
        assert updated["case_type"] == "after_sales" and updated["ticket_no"] == original_no
        assert ticket_id in {t["id"] for t in client.get("/api/v1/tickets").json()}
        with SessionLocal() as session:
            ticket = session.scalar(select(Ticket).where(Ticket.id == ticket_id))
            texts = [m["content"] for m in ticket.assessment["conversation"] if m["role"] == "customer"]
            assert texts == ["帮我找一款蓝牙耳机", "请转人工客服", "之前买的耳机坏了，我要申请退款"]
        assert client.get("/api/v1/tickets?case_type=invalid").status_code == 422
