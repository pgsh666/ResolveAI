import asyncio
import json
from pathlib import Path
from types import SimpleNamespace

import pytest
from pydantic import ValidationError

from app.schemas import BatchEvaluationRequest
from evaluation.fixtures import business_checks, fixture_snapshot, order_number
from evaluation.run import DEFAULT_DATASET, LEGACY_DATASET, load_cases, run_suite, validate_dataset
from evaluation.schema import EvaluationCase


def test_first_sixty_inputs_and_expectations_are_unchanged():
    legacy = [json.loads(line) for line in LEGACY_DATASET.read_text(encoding="utf-8").splitlines() if line]
    current = [json.loads(line) for line in DEFAULT_DATASET.read_text(encoding="utf-8").splitlines() if line]
    assert len(legacy) == 60 and current[:60] == legacy and len(current) == 120
    assert len({case["id"] for case in current}) == 120


def test_new_commerce_cases_have_fixtures_and_explicit_behavior_expectations():
    cases = load_cases()[60:]
    assert len(cases) == 60 and all(case.channel == "web" and case.fixture == "commerce-v1" for case in cases)
    assert all(case.expected_routes or case.expected_context or case.reply_excludes for case in cases)
    assert {"sold_out", "faq_disabled", "workspace_isolation", "ownership", "long_history", "scope", "topic_switch"} <= {
        tag for case in cases for tag in case.tags}


def test_dataset_size_validation_accepts_120_but_keeps_upper_bound():
    validate_dataset(load_cases())
    with pytest.raises(ValueError, match="200"):
        validate_dataset(load_cases() * 2)


def test_background_request_accepts_the_full_120_case_suite():
    assert BatchEvaluationRequest(limit=120).limit == 120
    with pytest.raises(ValidationError):
        BatchEvaluationRequest(limit=121)


def test_web_fixture_and_legacy_tool_adapters_cannot_be_mixed():
    with pytest.raises(ValidationError, match="versioned"):
        EvaluationCase(id="EVAL-121", category="product", scenario="ordinary", subject="商品", message="商品", channel="web")
    with pytest.raises(ValidationError, match="legacy MCP"):
        EvaluationCase(id="EVAL-121", category="product", scenario="tool_timeout", subject="商品", message="商品",
                       channel="web", fixture="commerce-v1", fault="tool_timeout")


def test_120_case_behavior_checks_detect_wrong_stock_hallucinations_and_wrong_routes():
    case = next(case for case in load_cases() if case.id == "EVAL-203")
    ticket = SimpleNamespace(draft_response="星砂罐已售罄")
    assessment = {"customer_context": {"assistant_route": "catalog", "catalog_intent": "availability",
                                      "catalog": [{"name": "星砂罐", "stock": 0}]}}
    assert all(business_checks(case, ticket, assessment).values())
    assessment["customer_context"]["catalog"][0]["stock"] = 8
    assert not business_checks(case, ticket, assessment)["stock_facts"]
    assessment["customer_context"]["assistant_route"] = "support"
    assert not business_checks(case, ticket, assessment)["business_route"]
    ticket.draft_response = "已经为您转接人工客服"
    assert not business_checks(case, ticket, assessment)["reply_forbidden_claims"]


def test_real_workflow_uses_isolated_web_fixtures_and_reports_cohorts():
    cases = [case for case in load_cases() if case.id in {"EVAL-203", "EVAL-224"}]
    updates = []
    report = asyncio.run(run_suite(cases, progress=updates.append))
    assert report["complete"] and report["evaluated_cases"] == 2
    assert report["cohorts"]["commerce"]["evaluated_cases"] == 2
    assert report["cohorts"]["legacy_support"]["evaluated_cases"] == 0
    assert report["fixture_snapshot"] == fixture_snapshot()
    assert len(updates) == 2 and updates[-1]["completed"] == 2 and updates[-1]["api_calls"] == 0
    for row in report["cases"]:
        assert row["observed"]["channel"] == "web"
        assert row["checks"]["end_to_end"]
        assert row["checks"]["business_route"]
        assert row["observed"]["tool"] is None
    assert report["metrics"]["business_behavior_accuracy"]["score"] == 1
    assert report["metrics"]["low_risk_handoff_rate"]["score"] == 0


def test_fixture_order_numbers_are_valid_and_distinct_across_cases():
    assert order_number("EVAL-079", 2) == "ORD-26-10-19-792"
    assert order_number("EVAL-080", 2) != order_number("EVAL-079", 2)
