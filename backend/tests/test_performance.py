from fastapi.testclient import TestClient

from app.core.config import Settings
from app.main import app
from app.services.llm import LLMService
from app.services.performance import PerformanceRegistry, latency_summary, percentile


def test_percentiles_and_latency_summary_use_nearest_rank():
    values = list(range(1, 101))
    assert percentile(values, 50) == 50
    assert percentile(values, 95) == 95
    assert latency_summary(values) == {
        "samples": 100,
        "p50_ms": 50,
        "p95_ms": 95,
        "average_ms": 50.5,
        "max_ms": 100.0,
    }


def test_rate_limit_has_deterministic_local_fallback():
    registry = PerformanceRegistry(
        Settings(
            app_env="test",
            agent_mode="demo",
            database_url="sqlite://",
            async_tasks_enabled=False,
            rate_limit_enabled=True,
            rate_limit_requests_per_minute=2,
        )
    )
    assert registry.check_rate_limit("test-client")[0] is True
    assert registry.check_rate_limit("test-client")[0] is True
    allowed, remaining, retry_after = registry.check_rate_limit("test-client")
    assert allowed is False
    assert remaining == 0
    assert 1 <= retry_after <= 60


def test_model_routing_and_stage_specific_costs():
    settings = Settings(
        app_env="test",
        agent_mode="deepseek",
        deepseek_api_key="test-key",
        database_url="sqlite://",
        deepseek_model="primary-model",
        deepseek_fast_model="economy-model",
        model_input_cost_per_million_usd=2,
        model_output_cost_per_million_usd=8,
        fast_model_input_cost_per_million_usd=1,
        fast_model_output_cost_per_million_usd=2,
    )
    service = LLMService(settings)
    usage = {"input_tokens": 1000, "output_tokens": 500, "total_tokens": 1500}

    economy = service._metadata(False, attempts=1, usage=usage, stage="triage")
    primary = service._metadata(False, attempts=1, usage=usage, stage="resolution")

    assert economy["model"] == "economy-model"
    assert economy["routing_tier"] == "economy"
    assert economy["estimated_cost_usd"] == 0.002
    assert primary["model"] == "primary-model"
    assert primary["routing_tier"] == "primary"
    assert primary["estimated_cost_usd"] == 0.006


def test_retrieval_cache_metrics_and_performance_endpoint():
    query = "仅供性能缓存测试的唯一退款知识查询-20260929"
    with TestClient(app) as client:
        first = client.get("/api/v1/knowledge/search", params={"q": query})
        second = client.get("/api/v1/knowledge/search", params={"q": query})
        metrics = client.get("/api/v1/performance/metrics")

    assert first.status_code == 200
    assert first.json()["cache_hit"] is False
    assert second.status_code == 200
    assert second.json()["cache_hit"] is True
    assert "X-Response-Time-Ms" in second.headers
    assert metrics.status_code == 200
    payload = metrics.json()
    assert payload["http"]["samples"] >= 2
    assert payload["retrieval_cache"]["hits"] >= 1
    assert payload["retrieval_cache"]["misses"] >= 1
    assert payload["limits"]["max_concurrent_requests"] == 32
