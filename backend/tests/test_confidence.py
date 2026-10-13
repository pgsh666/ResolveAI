import pytest

from app.services.confidence import calculate_confidence


def test_weighted_confidence_and_fallback_penalties():
    result = calculate_confidence(
        triage={"confidence": 0.9},
        citations=[{"score": 0.4}, {"score": 0.2}],
        customer_context={
            "customer_name": "测试客户",
            "customer_level": "VIP",
            "order_id": "ORD-1",
            "order_status": "delivered",
            "order_amount": 200,
            "paid": True,
            "tool_fallback": "MCP unavailable",
        },
        quality={"checks": {"citation": True, "action": True, "reply": False, "gate": True}},
        resolution={"confidence": 0.8},
        model_trace=[{"stage": "triage", "fallback": True}],
    )

    assert result["base_score"] == pytest.approx(0.745)
    assert result["penalty_total"] == pytest.approx(0.13)
    assert result["final_score"] == pytest.approx(0.615)
    assert result["level"] == "low"
    assert [item["key"] for item in result["penalties"]] == [
        "model_fallback",
        "mcp_fallback",
    ]
