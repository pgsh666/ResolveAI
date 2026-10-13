from collections import Counter
from pathlib import Path

from evaluation.run import (
    REQUIRED_CATEGORIES,
    REQUIRED_SCENARIOS,
    load_cases,
    render_markdown,
    validate_dataset,
)


def test_evaluation_dataset_has_required_size_and_coverage():
    cases = load_cases()
    validate_dataset(cases)

    assert len(cases) == 60
    assert {case.category for case in cases} == REQUIRED_CATEGORIES
    assert {case.scenario for case in cases} == REQUIRED_SCENARIOS
    assert Counter(case.category for case in cases) == {
        "refund": 12,
        "logistics": 12,
        "payment": 12,
        "product": 12,
        "account": 12,
    }


def test_tool_expectations_have_validated_arguments():
    cases = load_cases()
    tool_cases = [case for case in cases if case.expected_tool]

    assert tool_cases
    assert all(case.expected_tool == "get_order_context" for case in tool_cases)
    assert all(case.expected_tool_args == {"order_id": case.order_id} for case in tool_cases)


def test_markdown_report_contains_all_required_metrics():
    metric = {"numerator": 1, "denominator": 1, "score": 1.0, "display": "100.00%"}
    report = {
        "generated_at": "2026-01-01T00:00:00+00:00",
        "execution_mode": "test",
        "passed_cases": 1,
        "evaluated_cases": 1,
        "coverage": {"categories": {"refund": 1}, "scenarios": {"ordinary": 1}},
        "metrics": {
            "intent_accuracy": metric,
            "rag_recall_at_k": metric,
            "citation_accuracy": metric,
            "tool_selection_accuracy": metric,
            "tool_argument_accuracy": metric,
            "high_risk_block_rate": metric,
            "end_to_end_completion_rate": metric,
            "unapproved_write_count": {"count": 0, "target": 0, "passed": True},
        },
        "cases": [],
    }

    markdown = render_markdown(report)

    assert "意图识别准确率" in markdown
    assert "RAG Recall@K" in markdown
    assert "工作流工具参数符合率" in markdown
    assert "非客户解决率" in markdown
    assert "高风险拦截率" in markdown
    assert "未审批写操作次数" in markdown


def test_dataset_is_portable_jsonl_file():
    path = Path(__file__).parents[1] / "evaluation" / "cases.jsonl"
    lines = [line for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
    assert len(lines) == 60
