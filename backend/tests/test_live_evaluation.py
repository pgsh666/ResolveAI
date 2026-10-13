import asyncio
import json
import os
import subprocess
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from app.core.config import Settings
from app.services.llm import LLMService
from evaluation.live import (
    EvaluationLLM, LiveCallError, LiveRun, StrictModelFailure,
    evaluation_settings, public_config, summarize_calls,
)
from evaluation.run import (
    _configure_isolated_environment, load_cases, main, render_markdown, run_suite, write_reports,
)


def live_settings(**overrides):
    values = {name: item.default for name, item in Settings.model_fields.items()}
    values.update(
        app_env="test", agent_mode="deepseek", deepseek_api_key="unit-test-key",
        database_url="sqlite://", model_max_retries=0, retry_backoff_seconds=0,
        model_input_cost_per_million_usd=1, model_output_cost_per_million_usd=2,
        fast_model_input_cost_per_million_usd=1, fast_model_output_cost_per_million_usd=2,
    )
    values.update(overrides)
    return Settings(_env_file=None, **values)


def completion(content=None, *, finish="stop", usage=True):
    if content is None:
        content = LLMService._demo_triage("退款咨询", "退款还未到账").model_dump_json()
    return SimpleNamespace(
        id="unit-response-id", model="deepseek-unit-test",
        usage=SimpleNamespace(prompt_tokens=10, completion_tokens=5, total_tokens=15) if usage else None,
        choices=[SimpleNamespace(message=SimpleNamespace(content=content), finish_reason=finish)],
    )


def service(monkeypatch, *, responses=None, settings=None, budget=20):
    create = Mock(side_effect=responses) if responses is not None else Mock(return_value=completion())
    client = SimpleNamespace(
        chat=SimpleNamespace(completions=SimpleNamespace(create=create)), close=Mock(),
    )
    monkeypatch.setattr("app.services.llm.OpenAI", lambda **kwargs: client)
    run = LiveRun(budget)
    return EvaluationLLM(settings or live_settings(), run, "EVAL-001"), run, create


def test_live_response_has_evidence_and_usage(monkeypatch):
    llm, run, create = service(monkeypatch)
    assert llm.triage("退款", "退款未到账").category == "refund"
    assert create.call_count == 1
    assert llm.valid_live_result()
    record = run.calls[0]
    assert record["response_id"] == "unit-response-id"
    assert record["requested_model"] == llm.settings.deepseek_fast_model
    assert record["total_tokens"] == 15
    assert record["status"] == "success"
    assert len(record["system_prompt_sha256"]) == 64
    assert "unit-test-key" not in json.dumps(record)


def test_strict_evaluator_refuses_demo_settings():
    settings = live_settings(agent_mode="demo", deepseek_api_key=None)
    with pytest.raises(ValueError, match="只接受"):
        EvaluationLLM(settings, LiveRun(20), "EVAL-001")


@pytest.mark.parametrize("response", [
    completion(""), completion("not-json"), completion("{}"),
    completion(finish="length"), completion(usage=False),
    SimpleNamespace(id=None, model="deepseek-unit-test", usage=completion().usage, choices=completion().choices),
])
def test_invalid_live_response_never_uses_demo(monkeypatch, response):
    llm, run, _ = service(monkeypatch, responses=[response])
    monkeypatch.setattr(llm, "_demo_triage", Mock(side_effect=AssertionError("must not fallback")))
    with pytest.raises(StrictModelFailure):
        llm.triage("退款", "退款未到账")
    assert not llm.valid_live_result()
    assert run.calls[0]["status"] == "error"
    assert llm.call_metadata["triage"]["fallback"] is True


def test_usage_includes_invalid_json_and_retry(monkeypatch):
    llm, run, create = service(
        monkeypatch, responses=[completion("broken JSON"), completion()],
        settings=live_settings(model_max_retries=1),
    )
    llm.triage("退款", "退款未到账")
    assert create.call_count == 2
    assert llm.call_metadata["triage"]["retry_count"] == 1
    summary = summarize_calls(run)
    assert summary["total_tokens"] == 30
    assert summary["failed_calls"] == 1
    assert summary["successful_calls"] == 1
    assert summary["cost_complete"] is True


@pytest.mark.parametrize("status_code", [400, 401, 402, 403, 404, 422])
def test_fatal_errors_stop_without_retry(monkeypatch, status_code):
    llm, run, create = service(
        monkeypatch, responses=[LiveCallError("bad unit-test-key", status_code)],
        settings=live_settings(model_max_retries=2),
    )
    with pytest.raises(StrictModelFailure):
        llm.triage("退款", "退款未到账")
    assert create.call_count == 1
    assert llm.call_metadata["triage"]["attempts"] == 1
    assert run.abort_reason
    assert "unit-test-key" not in json.dumps(run.calls)


def test_api_call_cap_includes_retries(monkeypatch):
    llm, run, create = service(
        monkeypatch, responses=[TimeoutError("offline timeout")],
        settings=live_settings(model_max_retries=2), budget=1,
    )
    with pytest.raises(StrictModelFailure):
        llm.triage("退款", "退款未到账")
    assert create.call_count == 1
    assert len(run.calls) == 1
    assert run.abort_reason
    assert summarize_calls(run)["usage_unknown_calls"] == 1


def test_unknown_prices_are_not_reported_as_free(monkeypatch):
    llm, run, _ = service(monkeypatch, settings=live_settings(
        fast_model_input_cost_per_million_usd=0, fast_model_output_cost_per_million_usd=0,
    ))
    llm.triage("退款", "退款未到账")
    assert summarize_calls(run)["known_estimated_cost_usd"] is None
    assert summarize_calls(run)["cost_complete"] is False


def test_live_execution_requires_paid_opt_in(capsys, monkeypatch):
    monkeypatch.setattr("app.services.llm.OpenAI", Mock(side_effect=AssertionError("no API client")))
    assert main(["--provider", "deepseek", "--smoke"]) == 2
    assert "allow-paid-api" in capsys.readouterr().err


def test_dry_run_is_balanced_and_does_not_create_db_or_client(capsys, tmp_path, monkeypatch):
    monkeypatch.setattr("app.services.llm.OpenAI", Mock(side_effect=AssertionError("no API client")))
    assert main(["--provider", "deepseek", "--smoke", "--dry-run", "--output-dir", str(tmp_path)]) == 0
    plan = json.loads(capsys.readouterr().out)
    assert plan["selected_cases"] == 5
    assert set(plan["categories"].values()) == {1}
    assert len(plan["categories"]) == 5
    assert plan["api_calls"] == 0
    assert plan["database_created"] is False
    assert list(tmp_path.iterdir()) == []
    assert "DEEPSEEK_API_KEY" not in json.dumps(plan)


def test_configuration_does_not_inherit_business_services(monkeypatch):
    monkeypatch.setenv("DATABASE_URL", "postgresql://do-not-connect/business")
    monkeypatch.setenv("ASYNC_TASKS_ENABLED", "true")
    monkeypatch.setenv("MCP_ENABLED", "true")
    monkeypatch.setenv("OTEL_ENABLED", "true")
    monkeypatch.setenv("DEEPSEEK_API_KEY", "local-test-key")
    settings = evaluation_settings(None, require_key=True)
    assert settings.database_url == "sqlite://"
    assert not settings.mcp_enabled
    assert not settings.async_tasks_enabled
    assert not settings.otel_enabled
    assert not settings.retrieval_cache_enabled
    assert "local-test-key" not in json.dumps(public_config(settings))


@pytest.mark.parametrize("endpoint", [
    "http://api.deepseek.com", "https://example.com", "https://api.deepseek.com.evil.test",
    "https://user:password@api.deepseek.com", "https://api.deepseek.com?key=secret",
])
def test_untrusted_key_destinations_are_rejected(monkeypatch, endpoint):
    monkeypatch.setenv("DEEPSEEK_BASE_URL", endpoint)
    with pytest.raises(ValueError, match="官方"):
        evaluation_settings(None, require_key=False)


def test_missing_key_has_safe_error(monkeypatch):
    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)
    with pytest.raises(ValueError, match="缺少 DEEPSEEK_API_KEY"):
        evaluation_settings(None, require_key=True)


def test_loaded_business_database_is_refused(tmp_path):
    with pytest.raises(RuntimeError, match="独立进程"):
        _configure_isolated_environment(tmp_path / "must-not-create.db")
    assert not (tmp_path / "must-not-create.db").exists()


def test_existing_database_is_never_overwritten(tmp_path, monkeypatch):
    database = tmp_path / "existing.db"
    database.write_bytes(b"existing user data")
    monkeypatch.delitem(os.sys.modules, "app.core.database", raising=False)
    with pytest.raises(ValueError, match="拒绝覆盖"):
        _configure_isolated_environment(database)
    assert database.read_bytes() == b"existing user data"


def test_smoke_cannot_be_combined_with_limit():
    with pytest.raises(ValueError, match="混用"):
        main(["--smoke", "--limit", "5"])


def test_mock_live_suite_reports_evidence_and_partial_abort(monkeypatch, tmp_path):
    # Real workflow/checkpoints, fake SDK responses: this test never contacts DeepSeek.
    from app.core.database import init_db

    init_db()
    llm, run, create = service(monkeypatch, responses=[LiveCallError("unauthorized", 401)])
    monkeypatch.setattr("evaluation.live.EvaluationLLM", lambda settings, live, case_id: llm)
    cases = load_cases()[:3]
    report = asyncio.run(run_suite(cases, live=run))
    assert create.call_count == 1
    assert report["complete"] is False
    assert report["evaluated_cases"] == 1
    assert report["requested_cases"] == 3
    assert report["not_run_case_ids"] == [case.id for case in cases[1:]]
    assert report["metrics"]["intent_accuracy"]["score"] == 0
    assert report["metrics"]["draft_workflow_success_rate"]["score"] == 0
    assert report["cases"][0]["checks"]["live_model"] is False
    assert report["manual_review_status"] == "pending"
    write_reports(report, tmp_path)
    assert (tmp_path / "manual-review.json").is_file()
    markdown = render_markdown(report)
    assert "不得作为完整评测成绩" in markdown
    assert "不是免费" in markdown
    assert "非客户解决率" in markdown
    assert "不会调用 DeepSeek" not in markdown


def test_mock_live_suite_runs_complete_workflow_with_no_rules_fallback(monkeypatch):
    from app.core.database import init_db

    init_db()
    run = LiveRun(20)

    def create(**kwargs):
        payload = json.loads(kwargs["messages"][-1]["content"])
        if "trusted_business_policy" in payload:
            result = LLMService._demo_resolution(
                payload["user_input_untrusted"], payload["trusted_tool_context"],
                payload["trusted_business_policy"],
            )
        else:
            result = LLMService._demo_triage(payload["subject"], payload["message"])
        return completion(result.model_dump_json())

    client = SimpleNamespace(
        chat=SimpleNamespace(completions=SimpleNamespace(create=create)), close=Mock(),
    )
    monkeypatch.setattr("app.services.llm.OpenAI", lambda **kwargs: client)
    original = EvaluationLLM
    monkeypatch.setattr("evaluation.live.EvaluationLLM", lambda settings, run, case_id: original(
        live_settings(), run, case_id,
    ))
    cases = [load_cases()[12], load_cases()[24]]
    report = asyncio.run(run_suite(cases, live=run))
    assert report["complete"] is True
    assert report["evaluated_cases"] == 2
    assert report["metrics"]["live_model_case_rate"]["score"] == 1
    assert report["live_summary"]["api_calls"] >= 2
    assert all(row["provider"] == "deepseek" for row in report["calls"])
    assert report["metrics"]["rag_hit_at_k"]["denominator"] == 2
    assert report["metrics"]["rag_recall_at_k"]["denominator"] == sum(
        len(set(case.expected_sources)) for case in cases
    )


def test_cli_dry_run_in_fresh_process(tmp_path):
    backend = Path(__file__).parents[1]
    result = subprocess.run(
        [os.sys.executable, "-m", "evaluation.run", "--provider", "deepseek",
         "--smoke", "--dry-run", "--output-dir", str(tmp_path)],
        cwd=backend, capture_output=True, text=True, encoding="utf-8",
        env={**os.environ, "PYTHONIOENCODING": "utf-8"}, timeout=30,
    )
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout)["api_calls"] == 0
    assert list(tmp_path.iterdir()) == []
