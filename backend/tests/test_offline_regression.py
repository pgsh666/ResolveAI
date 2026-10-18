"""Regression coverage for CI triage, risk escalation and evaluation isolation."""

import os
import subprocess
import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.services.intent import classify_support_intent
from app.services.reply_validation import requests_money_operation
from app.services.retrieval_query import build_retrieval_query
from app.services.shop_assistant import handoff_reasons
from evaluation.run import load_cases


@pytest.mark.parametrize("subject,message,expected", [
    ("物流咨询", "客户：包裹有破损。\n客服：您可以申请退款。\n客户：照片补充好了，请继续。", "logistics"),
    ("在线咨询", "客户：支付时资金被冻结。\n客服：请耐心等待。\n客户：现在仍未释放。", "payment"),
    ("在线咨询", "客户：我无法登录账号。\n客服：请核对验证方式。\n客户：原手机号收不到验证码。", "account"),
    ("在线咨询", "客户：商品少了配件。\n客服：缺少什么？\n客户：没有充电器。", "product"),
    ("申请退货", "请问要在哪里提交？", "refund"),
    ("申请退款", "商品不合适，请问要怎么申请？", "refund"),
    ("在线咨询", "订单有四件商品，包裹里只有三件。", "product"),
    ("在线咨询", "请核对出库记录，补发缺失配件。", "product"),
    ("在线咨询", "快递包裹丢失，请补发。", "logistics"),
    ("退款进度", "客户：我想退货。\n客服：先看一下物流。\n客户：帮我选一款无线鼠标。", "product"),
    ("查看商品", "客户：我想买键盘。\n客服：看看商品详情。\n客户：我要退款。", "refund"),
    ("退款咨询", "客户：我要退货。\n客服：已记录。\n客户：帮我学习Python。", "other"),
    ("退款咨询", "客户：我要退货。\n客服：已记录。\n客户：有优惠券吗？", "other"),
    ("退款咨询", "客户：我要退货。\n客服：已记录。\n客户：您好！", "other"),
    ("在线咨询", "客户：商品到了。\n客服：请问需要退款、物流还是账号帮助？\n客户：这个颜色不好看。", "product"),
])
def test_fallback_triage_remembers_customer_context_but_not_stale_topics(subject, message, expected):
    assert classify_support_intent(subject, message) == expected


@pytest.mark.parametrize("message", ["同一笔被扣了两次，共518元", "重复扣款需要处理", "银行卡多扣了一笔"])
def test_large_duplicate_charge_is_handed_off_without_refund_keyword(message):
    reasons = handoff_reasons({"subject": "支付问题", "message": message, "amount": 518}, [], {})
    assert "大额或异常重复扣款需人工核验" in reasons


def test_claimed_amount_only_escalates_and_does_not_authorize_money_operation():
    ticket = {"message": "同一笔银行卡扣了两次，共518元"}
    assert handoff_reasons(ticket, [], {})
    assert not requests_money_operation(ticket)
    assert handoff_reasons({"message": "银行卡扣款两次", "amount": 89}, [], {}) == []
    assert handoff_reasons({"message": "银行卡扣款两次", "amount": 89}, [], {"order_amount": 250}) == []
    assert handoff_reasons({"message": "普通订单总价518元"}, [], {}) == []


@pytest.mark.parametrize("message", ["我想了解退款限制", "请介绍退货政策", "退款规则有哪些？"])
def test_policy_information_is_not_a_money_operation(message):
    ticket = {"subject": "售后政策咨询", "message": message}
    assert not requests_money_operation(ticket)
    assert handoff_reasons(ticket, [], {}) == []


def test_refund_request_is_not_hidden_by_policy_words():
    ticket = {"message": "我了解退款规则了，请帮我办理退款"}
    assert requests_money_operation(ticket)
    assert "退款或资金操作需人工处理" in handoff_reasons(ticket, [], {})


def test_customer_api_enforces_duplicate_charge_boundary():
    with TestClient(app) as client:
        response = client.post("/api/v1/customer/tickets", json={"message": "银行卡重复扣款，请核查518元"})
        assert response.status_code == 201, response.text
        ticket = response.json()
        assert ticket["handed_off"] is True
        assert "未执行退款" in ticket["sent_response"]


def test_query_expansion_uses_business_language_not_case_or_source_ids():
    query = build_retrieval_query({"category": "payment", "message": "订单扣了两笔，请核查"}, [])
    assert "重复扣款" in query and "支付流水" in query
    assert "EVAL-" not in query and "://" not in query


def test_refund_dataset_expectations_match_manual_boundary_and_keep_policy_consultation():
    cases = {case.id: case for case in load_cases()}
    for identifier in ("EVAL-001", "EVAL-002", "EVAL-003", "EVAL-004", "EVAL-008", "EVAL-011"):
        assert cases[identifier].expected_outcome == "human_review"
    assert cases["EVAL-010"].expected_outcome == "completed"


def test_default_offline_environment_ignores_developer_settings():
    script = """
from pathlib import Path
from tempfile import TemporaryDirectory
from evaluation.run import _configure_isolated_environment
with TemporaryDirectory() as directory:
    _configure_isolated_environment(Path(directory) / 'isolated.db')
    from app.core.config import get_settings
    settings = get_settings()
    assert settings.agent_mode == 'demo'
    assert settings.embedding_provider == 'local'
    assert settings.rag_max_documents == 3
    assert settings.rag_min_score == .15
    assert not settings.deepseek_api_key and not settings.openai_api_key
    assert settings.auth_bootstrap_users == []
    assert not settings.mcp_enabled and not settings.async_tasks_enabled
    assert not settings.otel_enabled and not settings.retrieval_cache_enabled
    assert settings.database_url.startswith('sqlite:///')
print('isolated')
"""
    env = {**os.environ, "AGENT_MODE": "deepseek", "DEEPSEEK_API_KEY": "test-not-a-real-key",
           "EMBEDDING_PROVIDER": "openai", "RAG_MAX_DOCUMENTS": "1", "RAG_MIN_SCORE": ".99"}
    result = subprocess.run([sys.executable, "-c", script], cwd=Path(__file__).parents[1],
                            env=env, capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "isolated"
