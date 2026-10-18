import json
from pathlib import Path
from uuid import uuid4

import pytest
from sqlalchemy import create_engine, text, select
from sqlalchemy.orm import Session
from app.core.config import Settings
from app.core.migrations import migrate_database
from app.services.costs import estimate_cost
from app.services.reply_guard import check_reply_evidence
from app.models import Ticket, ModelUsage, WorkflowRun, utc_now
from app.core.database import Base


@pytest.mark.parametrize("draft,check", [
    ("我们已经联系承运商核实情况。", "action_state_has_evidence"),
    ("正在为您补发商品。", "action_state_has_evidence"),
    ("已为您发放关怀券。", "action_state_has_evidence"),
    ("已收到商品照片。", "customer_claims_attributed"),
    ("预计7分钟内完成审核。", "time_promise_supported"),
    ("请打开【我的赔偿中心】。", "ui_entry_supported"),
    ("退款会在1至3个工作日到账。", "refund_promise_conditional"),
])
def test_unsupported_promises_fail_closed(draft, check):
    checks = check_reply_evidence({"order_id": "ORD-1"}, {}, [], draft)
    assert checks[check] is False


def test_fallback_not_verified_and_simulation_not_real():
    assert not check_reply_evidence({}, {"tool_fallback": "unavailable"}, [], "经查询，订单已签收。")["fallback_not_claimed_verified"]
    ticket = {"order_id": "ORD-1", "completed_operations": [{"tool_name": "execute_refund", "result": {"success": True, "order_id": "ORD-1", "mode": "simulated"}}]}
    assert not check_reply_evidence(ticket, {}, [], "已完成退款。")["simulated_result_disclosed"]
    assert all(check_reply_evidence(ticket, {}, [], "已完成模拟退款，不代表真实到账。").values())


def test_conditional_policy_and_attribution_are_allowed():
    citations = [{"snippet": "审核通过后，退款通常1至3个工作日到账。"}]
    assert all(check_reply_evidence({}, {}, citations, "审核通过后，退款预计一至三个工作日到账。我们尚未联系承运商。").values())
    assert all(check_reply_evidence({}, {"order_status": "delayed"}, [], "您反馈商品已经送达，仍需核验。").values())


def test_cost_cache_unknown_and_demo():
    settings = Settings(_env_file=None, agent_mode="deepseek", deepseek_api_key="fake", app_env="test")
    usage = {"input_tokens": 1000, "output_tokens": 500, "cache_hit_tokens": 900, "cache_miss_tokens": 100}
    value = estimate_cost(settings, "deepseek-flash", usage)
    assert value["cost_complete"] and value["cache_usage_known"]
    assert value["cost_lower_usd"] < value["cost_upper_usd"]
    assert estimate_cost(settings, "unknown-model", usage)["estimated_cost_usd"] is None
    assert estimate_cost(settings, "deepseek-flash", usage, usage_available=False)["estimated_cost_usd"] is None
    settings.agent_mode = "demo"
    assert estimate_cost(settings, "deterministic-demo", usage)["estimated_cost_usd"] == 0


def test_migration_adopts_legacy_data_and_is_repeatable(tmp_path):
    engine = create_engine(f"sqlite:///{(tmp_path/'legacy.db').as_posix()}")
    migrate_database(engine, "0001")
    with engine.begin() as connection:
        connection.execute(text("INSERT INTO tickets (id,ticket_no,customer_name,customer_tier,channel,subject,message,status,priority,sla_minutes,requires_approval,created_at,updated_at) VALUES ('keep','TKT-KEEP','客户','standard','chat','保留数据','原始消息','new','medium',60,0,'2026-10-14 01:00:00','2026-10-14 01:00:00')"))
    with engine.begin() as connection:
        # Emulate a historical unversioned installation; never touch a business DB.
        connection.execute(text("DELETE FROM alembic_version"))
    migrate_database(engine)
    migrate_database(engine)
    with engine.connect() as connection:
        assert connection.scalar(text("SELECT message FROM tickets WHERE id='keep'")) == "原始消息"
        assert connection.scalar(text("SELECT version_num FROM alembic_version")) == "0004"
        assert connection.scalar(text("SELECT ticket_no FROM tickets WHERE id='keep'")) == "26-10-14-1"
        assert connection.scalar(text("SELECT legacy_ticket_no FROM tickets WHERE id='keep'")) == "TKT-KEEP"
        assert connection.scalar(text("SELECT count(*) FROM accounts")) == 0
    engine.dispose()


def test_ledger_survives_restart_and_counts_every_attempt(tmp_path, monkeypatch):
    from app.services.performance import build_performance_report
    from app.core.config import get_settings
    engine = create_engine(f"sqlite:///{(tmp_path/'ledger.db').as_posix()}")
    Base.metadata.create_all(engine)
    with Session(engine) as session:
        ticket = Ticket(ticket_no="LEDGER", customer_name="客户", subject="测试", message="测试", assessment={"model_trace": [{"model": "wrong-duplicate", "total_tokens": 999}]})
        session.add(ticket); session.commit()
        for index, status in enumerate(["error", "success", "success"]):
            usage = {"model": "deepseek-flash", "status": status, "input_tokens": 100, "output_tokens": 20, "total_tokens": 120,
                     "estimated_cost_usd": .001 if index else None, "cost_lower_usd": .0005, "cost_upper_usd": .001}
            session.add(ModelUsage(id=str(uuid4()), ticket_id=ticket.id, model="deepseek-flash", stage="rewrite", usage=usage))
        session.add(WorkflowRun(ticket_id=ticket.id, operation="analyze", submitted_at=utc_now(), started_at=utc_now(), queue_ms=50, processing_ms=100, end_to_end_ms=150, status="failed")); session.commit()
    engine.dispose()
    with Session(engine) as session:
        report = build_performance_report(session)
        assert report["models"]["calls"] == 3
        assert report["models"]["total_tokens"] == 360
        assert report["models"]["estimated_cost_usd"] is None
        assert report["models"]["unknown_cost_calls"] == 1
        assert report["models"]["known_estimated_cost_usd"] == .002
        assert report["models"]["complete_cost_calls"] == 2
        assert report["models"]["recorded_average_cost_per_call_usd"] == .001
        assert report["workflow"]["p50_ms"] == 150
        assert report["workflow"]["queue"]["p50_ms"] == 50
        assert report["workflow"]["failed_runs"] == 1
    engine.dispose()


def test_paid_parse_failure_retry_is_also_counted(monkeypatch):
    from app.services.llm import LLMService
    from app.schemas import TriageResult
    settings = Settings(_env_file=None, app_env="test", agent_mode="deepseek", deepseek_api_key="fake",
                        model_max_retries=1, retry_backoff_seconds=0)
    llm = LLMService(settings)
    ledger = []
    llm.usage_recorder = ledger.append
    def parse(*args):
        llm._last_usage_available = True
        llm._last_usage = {"input_tokens": 100, "output_tokens": 20, "total_tokens": 120}
        llm._last_response_model = "deepseek-flash"
        if not ledger: raise ValueError("Malformed paid response")
        return llm._demo_triage("退款", "退款未到账")
    monkeypatch.setattr(llm, "_parse", parse)
    result = llm.triage("退款", "退款未到账")
    assert len(ledger) == 2 and ledger[0]["status"] == "error" and ledger[1]["status"] == "success"
    assert llm.call_metadata["triage"]["total_tokens"] == 240
    assert llm.call_metadata["triage"]["estimated_cost_usd"] == sum(row["estimated_cost_usd"] for row in ledger)


def test_public_release_is_fail_closed():
    with pytest.raises(ValueError):
        Settings(_env_file=None, public_deployment=True)
    with pytest.raises(ValueError):
        Settings(_env_file=None, app_env="production", public_deployment=True, auth_cookie_secure=True,
                 cors_origins="https://example.invalid", tool_approval_secret="r"*43, database_url="postgresql+psycopg://a:weak@localhost/db")
    valid = Settings(_env_file=None, app_env="production", auth_enabled=True, public_deployment=True, auth_cookie_secure=True,
                     cors_origins="https://example.invalid", tool_approval_secret="r"*43,
                     database_url="postgresql+psycopg://a:"+"p"*24+"@localhost/db")
    assert valid.public_deployment
