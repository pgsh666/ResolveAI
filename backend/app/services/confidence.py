from typing import Any


COMPONENTS = (
    ("intent", "意图识别", 0.25),
    ("rag", "RAG 依据", 0.25),
    ("context", "上下文完整度", 0.15),
    ("quality", "质检结果", 0.20),
    ("resolution", "方案生成", 0.15),
)

CONTEXT_FIELDS = (
    "customer_name",
    "customer_level",
    "order_id",
    "order_status",
    "order_amount",
    "paid",
)


def _clamp(value: Any) -> float:
    try:
        return max(0.0, min(1.0, float(value)))
    except (TypeError, ValueError):
        return 0.0


def _is_present(value: Any) -> bool:
    if value is None:
        return False
    if isinstance(value, str):
        return bool(value.strip())
    if isinstance(value, (list, dict, tuple, set)):
        return bool(value)
    return True


def calculate_confidence(
    *,
    triage: dict[str, Any],
    citations: list[dict[str, Any]],
    customer_context: dict[str, Any],
    quality: dict[str, Any],
    resolution: dict[str, Any],
    model_trace: list[dict[str, Any]],
) -> dict[str, Any]:
    """Build an explainable, deterministic confidence score from workflow evidence."""

    top_citation_score = max(
        (_clamp(item.get("score")) for item in citations),
        default=0.0,
    )
    present_fields = sum(
        _is_present(customer_context.get(field)) for field in CONTEXT_FIELDS
    )
    context_score = present_fields / len(CONTEXT_FIELDS)

    checks = quality.get("checks") or {}
    quality_score = (
        sum(bool(value) for value in checks.values()) / len(checks)
        if checks
        else 0.0
    )

    scores = {
        "intent": _clamp(triage.get("confidence")),
        "rag": top_citation_score,
        "context": _clamp(context_score),
        "quality": _clamp(quality_score),
        "resolution": _clamp(resolution.get("confidence")),
    }
    evidence = {
        "intent": "分诊 Agent 的结构化置信度",
        "rag": f"最高知识库相似度 {top_citation_score:.0%}",
        "context": f"关键字段完整 {present_fields}/{len(CONTEXT_FIELDS)}",
        "quality": f"质检项通过 {sum(bool(value) for value in checks.values())}/{len(checks)}" if checks else "未生成质检项",
        "resolution": "方案生成 Agent 的结构化置信度",
    }
    components = [
        {
            "key": key,
            "label": label,
            "score": round(scores[key], 4),
            "weight": weight,
            "weighted_score": round(scores[key] * weight, 4),
            "evidence": evidence[key],
        }
        for key, label, weight in COMPONENTS
    ]
    base_score = sum(item["weighted_score"] for item in components)

    penalties: list[dict[str, Any]] = []
    fallback_stages = [
        str(item.get("stage", "模型调用"))
        for item in model_trace
        if item.get("fallback") is True
    ]
    if fallback_stages:
        penalties.append(
            {
                "key": "model_fallback",
                "label": "模型降级",
                "value": 0.08,
                "reason": f"{', '.join(fallback_stages)} 使用了本地规则兜底",
            }
        )
    if customer_context.get("tool_fallback"):
        penalties.append(
            {
                "key": "mcp_fallback",
                "label": "MCP 降级",
                "value": 0.05,
                "reason": "业务上下文由本地适配器兜底提供",
            }
        )

    penalty_total = sum(float(item["value"]) for item in penalties)
    final_score = _clamp(base_score - penalty_total)
    level = "high" if final_score >= 0.85 else "medium" if final_score >= 0.70 else "low"
    return {
        "version": "1.0",
        "method": "weighted_evidence",
        "components": components,
        "base_score": round(base_score, 4),
        "penalties": penalties,
        "penalty_total": round(penalty_total, 4),
        "final_score": round(final_score, 4),
        "level": level,
    }
