from uuid import uuid4

import pytest
from sqlalchemy import create_engine, select, func
from sqlalchemy.orm import Session

from app.core.database import Base
from app.models import ModelUsage, Ticket
from app.services.performance import build_performance_report


def add_usage(session, ticket, cost, **overrides):
    usage = {"model": "test-model", "provider": "deepseek", "status": "success",
             "input_tokens": 100, "output_tokens": 20, "total_tokens": 120,
             "usage_available": True, "cost_complete": True,
             "estimated_cost_usd": cost, "cost_lower_usd": cost, "cost_upper_usd": cost,
             **overrides}
    session.add(ModelUsage(id=str(uuid4()), ticket_id=ticket.id, stage="resolution", model="test-model", usage=usage))


def test_29_recorded_calls_ignore_12_legacy_snapshots_without_deleting_history():
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    with Session(engine) as session:
        ticket = Ticket(ticket_no="COST-NEW", customer_name="客户", subject="统计", message="统计")
        old = Ticket(ticket_no="COST-OLD", customer_name="客户", subject="历史", message="原文",
                     assessment={"model_trace": [{"model": "old-model", "total_tokens": 9999,
                                                  "estimated_cost_usd": 100} for _ in range(12)]})
        session.add_all([ticket, old]); session.flush()
        for _ in range(29):
            add_usage(session, ticket, .001)
        session.commit()
        models = build_performance_report(session)["models"]
        assert models["calls"] == models["ledger_calls"] == models["complete_cost_calls"] == 29
        assert models["total_tokens"] == 29 * 120
        assert models["legacy_trace_calls"] == 12
        assert models["unknown_cost_calls"] == 0
        assert models["recorded_average_cost_per_call_usd"] == .001
        assert models["average_cost_per_call_usd"] == .001
        assert models["known_estimated_cost_usd"] == models["estimated_cost_usd"] == .029
        assert models["cost_complete"] is True
        assert [row["model"] for row in models["by_model"]] == ["test-model"]
        assert len(session.get(Ticket, old.id).assessment["model_trace"]) == 12
        assert session.scalar(select(func.count()).select_from(ModelUsage)) == 29
        assert session.get(Ticket, old.id).message == "原文"
    engine.dispose()


def test_complete_cost_mean_keeps_zero_cost_and_charged_failures_but_excludes_unknown():
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    with Session(engine) as session:
        ticket = Ticket(ticket_no="COST-MIX", customer_name="客户", subject="统计", message="统计")
        session.add(ticket); session.flush()
        add_usage(session, ticket, 0, provider="demo")
        add_usage(session, ticket, .006, status="error")
        add_usage(session, ticket, None, usage_available=False, cost_complete=False)
        session.commit()
        models = build_performance_report(session)["models"]
        assert models["calls"] == 3
        assert models["complete_cost_calls"] == 2
        assert models["recorded_average_cost_per_call_usd"] == .003
        assert models["known_estimated_cost_usd"] == .006
        assert models["unknown_cost_calls"] == models["failed_attempts"] == 1
        # The full-ledger amount remains unknown; the clearly scoped recorded mean is usable.
        assert models["average_cost_per_call_usd"] is None
        assert models["estimated_cost_usd"] is None
        assert models["cost_complete"] is False
    engine.dispose()


@pytest.mark.parametrize("rows", [[], [{"model": "old", "total_tokens": 3000, "estimated_cost_usd": 99}]])
def test_no_complete_ledger_records_does_not_show_a_zero_average(rows):
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    with Session(engine) as session:
        session.add(Ticket(ticket_no="COST-EMPTY", customer_name="客户", subject="统计", message="统计",
                           assessment={"model_trace": rows}))
        session.commit()
        models = build_performance_report(session)["models"]
        assert models["calls"] == models["complete_cost_calls"] == 0
        assert models["recorded_average_cost_per_call_usd"] is None
        assert models["average_cost_per_call_usd"] is None
        assert models["legacy_trace_calls"] == len(rows)
    engine.dispose()


@pytest.mark.parametrize("overrides", [{"estimated_cost_usd": -1}, {"estimated_cost_usd": True},
                                      {"cost_complete": False}, {"usage_available": False}])
def test_invalid_or_explicitly_incomplete_costs_are_not_used(overrides):
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    with Session(engine) as session:
        ticket = Ticket(ticket_no="COST-INVALID", customer_name="客户", subject="统计", message="统计")
        session.add(ticket); session.flush()
        add_usage(session, ticket, .001, **overrides)
        session.commit()
        models = build_performance_report(session)["models"]
        assert models["complete_cost_calls"] == 0
        assert models["known_estimated_cost_usd"] == 0
        assert models["recorded_average_cost_per_call_usd"] is None
        assert models["unknown_cost_calls"] == 1
    engine.dispose()
