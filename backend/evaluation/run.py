from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from collections import Counter
from datetime import datetime, timezone
from hashlib import sha256
from pathlib import Path
from typing import Any
from uuid import uuid4

from evaluation.schema import CaseResult, EvaluationCase, MetricResult


EVALUATION_DIR = Path(__file__).resolve().parent
LEGACY_DATASET = EVALUATION_DIR / "cases.jsonl"
DEFAULT_DATASET = EVALUATION_DIR / "cases_v3_120.jsonl"
DEFAULT_OUTPUT_DIR = EVALUATION_DIR / "reports" / "v3-120"
REQUIRED_CATEGORIES = {"refund", "logistics", "payment", "product", "account"}
REQUIRED_SCENARIOS = {
    "ordinary",
    "multi_turn",
    "missing_information",
    "high_risk",
    "unauthorized",
    "prompt_injection",
    "tool_timeout",
    "no_answer",
}


def load_cases(path: Path = DEFAULT_DATASET) -> list[EvaluationCase]:
    cases: list[EvaluationCase] = []
    seen_ids: set[str] = set()
    with path.open("r", encoding="utf-8") as handle:
        for line_number, raw_line in enumerate(handle, start=1):
            line = raw_line.strip()
            if not line:
                continue
            try:
                case = EvaluationCase.model_validate_json(line)
            except Exception as error:
                raise ValueError(f"{path}:{line_number} 不是合法评测用例：{error}") from error
            if case.id in seen_ids:
                raise ValueError(f"评测用例 ID 重复：{case.id}")
            seen_ids.add(case.id)
            cases.append(case)
    return cases


def validate_dataset(cases: list[EvaluationCase], *, holdout: bool = False) -> None:
    minimum = 20 if holdout else 50
    if not minimum <= len(cases) <= 200:
        raise ValueError(f"完整评测集必须包含 {minimum}～200 条用例，当前为 {len(cases)} 条")
    categories = {case.category for case in cases}
    scenarios = {case.scenario for case in cases}
    missing_categories = REQUIRED_CATEGORIES - categories
    missing_scenarios = REQUIRED_SCENARIOS - scenarios
    if missing_categories:
        raise ValueError(f"评测集缺少业务分类：{sorted(missing_categories)}")
    if missing_scenarios:
        raise ValueError(f"评测集缺少场景：{sorted(missing_scenarios)}")


def _configure_isolated_environment(database_path: Path, settings=None) -> None:
    if "app.core.database" in sys.modules:
        raise RuntimeError("评测必须在独立进程运行，禁止复用已加载的业务数据库连接")
    database_path.parent.mkdir(parents=True, exist_ok=True)
    if database_path.exists():
        raise ValueError("评测数据库路径已存在，拒绝覆盖或删除")
    os.environ["APP_ENV"] = "test"
    from app.core.config import Settings, get_settings

    # This helper is restricted to an independent evaluation process. Prevent
    # subsequent Settings() instances from even opening private dotenv files.
    Settings.model_config["env_file"] = None
    if settings is None:
        # The default CI rules baseline must be reproducible without reading
        # backend/.env or inheriting a developer's RAG/model/API configuration.
        values = {name: field.default for name, field in Settings.model_fields.items()}
        values.update(app_env="test", auth_enabled=False, agent_mode="demo", embedding_provider="local")
        settings = Settings(_env_file=None, **values)
    if settings is not None:
        # Explicit defaults prevent backend/.env and ambient Docker settings leaking in.
        for name, value in settings.model_dump().items():
            os.environ[name.upper()] = json.dumps(value) if isinstance(value, (list, dict)) else "" if value is None else str(value)
    os.environ.update(
        APP_ENV="test",
        AUTH_ENABLED="false",
        AGENT_MODE=settings.agent_mode if settings is not None else "demo",
        DATABASE_URL=f"sqlite:///{database_path.as_posix()}",
        MCP_ENABLED="false",
        RETRY_BACKOFF_SECONDS=str(settings.retry_backoff_seconds) if settings is not None else "0",
        ASYNC_TASKS_ENABLED="false",
        RETRIEVAL_CACHE_ENABLED="false",
        OTEL_ENABLED="false",
        OTEL_CONSOLE_EXPORTER="false",
    )
    get_settings.cache_clear()


def _metric(numerator: int, denominator: int) -> MetricResult:
    score = round(numerator / denominator, 4) if denominator else None
    display = "N/A" if score is None else f"{score:.2%}"
    return MetricResult(
        numerator=numerator,
        denominator=denominator,
        score=score,
        display=display,
    )


def _citation_is_valid(citation: dict, documents_by_source: dict[str, Any]) -> bool:
    document = documents_by_source.get(citation.get("source"))
    if document is None or citation.get("id") != document.id:
        return False
    snippet = str(citation.get("snippet") or "").removesuffix("…").strip()
    return bool(snippet) and snippet in document.content


def _expected_outcome_matches(case: EvaluationCase, ticket, assessment: dict) -> bool:
    if case.expected_outcome == "completed":
        return ticket.status == "in_progress" and bool(ticket.draft_response)
    if case.expected_outcome == "human_review":
        return ticket.status == "pending_approval" and ticket.requires_approval
    resilience = assessment.get("resilience") or {}
    rag = resilience.get("rag") or {}
    return (
        ticket.status == "pending_approval"
        and ticket.requires_approval
        and rag.get("reliable") is False
        and (assessment.get("resolution") or {}).get("safe_refusal") is True
        and bool(ticket.draft_response)
    )


async def run_suite(cases: list[EvaluationCase], *, live=None, progress=None) -> dict[str, Any]:
    # App imports deliberately happen after the isolated environment is configured.
    from sqlalchemy import select

    from app.core.config import Settings, get_settings
    from app.core.database import SessionLocal, engine, init_db
    from app.models import (
        Approval,
        KnowledgeDocument,
        Ticket,
        ToolExecution,
        WorkflowCheckpoint,
        new_id,
        utc_now,
    )
    from app.services.commerce import CommerceGateway
    from app.services.seed import seed_database
    from app.services.workflow import WorkflowService
    from app.services.embeddings import EmbeddingService
    from evaluation.fixtures import prepare_case, business_checks, fixture_snapshot

    get_settings.cache_clear()
    init_db()

    class RecordingCommerceGateway(CommerceGateway):
        def __init__(self, settings: Settings) -> None:
            super().__init__(settings)
            self.calls: list[dict[str, Any]] = []

        async def aget_customer_context(
            self, order_id: str | None, customer_name: str
        ) -> dict[str, Any]:
            if order_id:
                self.calls.append(
                    {
                        "tool_name": "get_order_context",
                        "arguments": {"order_id": order_id},
                    }
                )
            return await super().aget_customer_context(order_id, customer_name)

    class TimeoutCommerceGateway(RecordingCommerceGateway):
        async def _call_mcp(self, tool_name: str, arguments: dict[str, Any]) -> dict:
            raise TimeoutError(f"evaluation injected timeout for {tool_name}")

    class NoAnswerRetrieval:
        @staticmethod
        def search_reliable(query: str, limit: int = 3):
            return [], {
                "reliable": False,
                "threshold": get_settings().rag_min_score,
                "top_score": 0.0,
                "candidate_count": 0,
                "reliable_count": 0,
                "failure_reason": "评测注入：知识库无可靠答案",
            }

    case_results: list[CaseResult] = []
    metric_checks: dict[str, list[bool]] = {
        "intent_accuracy": [],
        "rag_recall_at_k": [],
        "citation_accuracy": [],
        "tool_selection_accuracy": [],
        "tool_argument_accuracy": [],
        "high_risk_block_rate": [],
        "end_to_end_completion_rate": [],
        "business_behavior_accuracy": [],
        "low_risk_handoff_rate": [],
    }
    evaluated_ticket_ids: list[str] = []
    relevant_hits = 0
    relevant_total = 0
    live_checks: list[bool] = []

    with SessionLocal() as session:
        seed_database(session)
        documents = session.scalars(select(KnowledgeDocument)).all()
        documents_by_source = {document.source: document for document in documents}
        knowledge_snapshot = {
            "documents": len(documents),
            "sha256": sha256(json.dumps(
                sorted((d.source, d.title, d.content) for d in documents), ensure_ascii=False,
            ).encode()).hexdigest(),
        }

        for case in cases:
            ticket = Ticket(
                ticket_no=case.id,
                customer_name=f"评测用户-{case.id[-3:]}",
                customer_tier="standard",
                channel=case.channel,
                subject=case.subject,
                message=case.message,
                order_id=case.order_id,
                amount=case.amount,
                status="new",
            )
            if case.history:
                now = utc_now().isoformat()
                conversation = [
                    {
                        "id": new_id(),
                        "role": item.role,
                        "content": item.content,
                        "created_at": now,
                    }
                    for item in case.history
                ]
                conversation.append(
                    {
                        "id": new_id(),
                        "role": "customer",
                        "content": case.message,
                        "created_at": now,
                    }
                )
                ticket.assessment = {"conversation": conversation}
            session.add(ticket)
            session.commit()
            session.refresh(ticket)
            if case.fixture:
                prepare_case(session, case, ticket)
                documents_by_source.update({document.source: document
                    for document in session.scalars(select(KnowledgeDocument)).all()})
            evaluated_ticket_ids.append(ticket.id)

            gateway_settings = Settings(
                _env_file=None,
                app_env="test",
                agent_mode="demo",
                database_url=get_settings().database_url,
                mcp_enabled=case.fault == "tool_timeout",
                tool_max_retries=2,
                retry_backoff_seconds=0,
            )
            gateway: RecordingCommerceGateway
            if case.fault == "tool_timeout":
                gateway = TimeoutCommerceGateway(gateway_settings)
            else:
                gateway = RecordingCommerceGateway(gateway_settings)

            dependencies: dict[str, Any] = {"commerce": gateway}
            live_llm = None
            if live is not None:
                from evaluation.live import EvaluationLLM

                live_llm = EvaluationLLM(get_settings(), live, case.id)
                dependencies["llm"] = live_llm
            if case.fault == "rag_no_answer":
                dependencies["retrieval"] = NoAnswerRetrieval()

            error_message = None
            try:
                await WorkflowService(
                    session,
                    graph_dependencies=dependencies,
                ).analyze(ticket)
                session.refresh(ticket)
            except Exception as error:  # keep the suite running and expose the failure
                session.rollback()
                error_message = f"{type(error).__name__}: {error}"
                ticket = session.get(Ticket, ticket.id)

            live_ok = error_message is None and (
                live_llm is None or live_llm.valid_live_result()
            )
            if live_llm is not None:
                live_checks.append(live_ok)
                live_llm.client.close()

            assessment = dict(ticket.assessment or {}) if ticket else {}
            citations = list(assessment.get("citations") or [])
            checkpoint = session.scalar(
                select(WorkflowCheckpoint).where(WorkflowCheckpoint.ticket_id == ticket.id)
            )
            executions = list(
                session.scalars(
                    select(ToolExecution).where(ToolExecution.ticket_id == ticket.id)
                ).all()
            )

            observed_tool = gateway.calls[0]["tool_name"] if gateway.calls else None
            observed_arguments = gateway.calls[0]["arguments"] if gateway.calls else {}
            observed_category = live_llm.triage_category if live_llm else ticket.category
            intent_ok = observed_category == case.category
            metric_checks["intent_accuracy"].append(intent_ok)

            rag_ok: bool | None = None
            if case.expected_sources:
                returned_sources = {str(item.get("source")) for item in citations}
                rag_ok = bool(returned_sources.intersection(case.expected_sources))
                metric_checks["rag_recall_at_k"].append(rag_ok)
                relevant_hits += len(returned_sources.intersection(case.expected_sources))
                relevant_total += len(set(case.expected_sources))

            citation_checks = [
                _citation_is_valid(citation, documents_by_source) for citation in citations
            ]
            metric_checks["citation_accuracy"].extend(citation_checks)
            citations_ok = all(citation_checks) if citation_checks else None

            tool_selection_ok = observed_tool == case.expected_tool
            metric_checks["tool_selection_accuracy"].append(tool_selection_ok)
            tool_arguments_ok: bool | None = None
            if case.expected_tool:
                tool_arguments_ok = observed_arguments == case.expected_tool_args
                metric_checks["tool_argument_accuracy"].append(tool_arguments_ok)

            high_risk_ok: bool | None = None
            if case.high_risk:
                high_risk_ok = (
                    ticket.status == "pending_approval"
                    and ticket.requires_approval
                    and not executions
                )
                metric_checks["high_risk_block_rate"].append(high_risk_ok)

            e2e_ok = (
                error_message is None
                and live_ok
                and checkpoint is not None
                and _expected_outcome_matches(case, ticket, assessment)
            )
            metric_checks["end_to_end_completion_rate"].append(e2e_ok)
            behavior_checks = business_checks(case, ticket, assessment)
            if behavior_checks:
                metric_checks["business_behavior_accuracy"].append(error_message is None and all(behavior_checks.values()))
            if case.channel == "web" and case.expected_outcome == "completed":
                metric_checks["low_risk_handoff_rate"].append(bool(ticket.requires_approval))

            applicable_checks = [
                intent_ok,
                tool_selection_ok,
                e2e_ok,
                *([] if rag_ok is None else [rag_ok]),
                *([] if citations_ok is None else [citations_ok]),
                *([] if tool_arguments_ok is None else [tool_arguments_ok]),
                *([] if high_risk_ok is None else [high_risk_ok]),
                *([] if live_llm is None else [live_ok]),
                *behavior_checks.values(),
            ]
            case_results.append(
                CaseResult(
                    id=case.id,
                    category=case.category,
                    scenario=case.scenario,
                    passed=all(applicable_checks),
                    error=error_message,
                    checks={
                        "intent": intent_ok,
                        "rag_recall_at_k": rag_ok,
                        "citations_grounded": citations_ok,
                        "tool_selection": tool_selection_ok,
                        "tool_arguments": tool_arguments_ok,
                        "high_risk_blocked": high_risk_ok,
                        "end_to_end": e2e_ok,
                        **({"live_model": live_ok} if live_llm else {}),
                        **behavior_checks,
                    },
                    observed={
                        "category": observed_category,
                        "channel": case.channel,
                        "status": ticket.status,
                        "requires_approval": ticket.requires_approval,
                        "citation_sources": [item.get("source") for item in citations],
                        "tool": observed_tool,
                        "tool_arguments": observed_arguments,
                        "checkpoint_status": checkpoint.status if checkpoint else None,
                        "draft_response": ticket.draft_response,
                        "citations": citations,
                        "model_trace": assessment.get("model_trace", []),
                        "customer_context": assessment.get("customer_context", {}),
                        "policy": assessment.get("policy", {}),
                        "resolution": assessment.get("resolution", {}),
                        "quality": assessment.get("quality", {}),
                        "rag_status": (assessment.get("resilience") or {}).get("rag", {}),
                        **({"live_calls": live_llm.records} if live_llm else {}),
                        **({"fallback_prevented": any(
                            value.get("fallback") for value in live_llm.call_metadata.values()
                        )} if live_llm else {}),
                        "failure_codes": [
                            item.get("code")
                            for item in (
                                (assessment.get("resilience") or {}).get("failures") or []
                            )
                        ],
                    },
                )
            )
            if progress is not None:
                progress({"completed": len(case_results), "requested": len(cases), "last_case": case.id,
                          "passed_so_far": sum(item.passed for item in case_results),
                          "api_calls": len(live.calls) if live else 0,
                          "max_api_calls": live.max_api_calls if live else 0})
            if live is not None and live.abort_reason:
                break

        all_executions = list(
            session.scalars(
                select(ToolExecution).where(ToolExecution.ticket_id.in_(evaluated_ticket_ids))
            ).all()
        )
        approvals = {
            approval.ticket_id: approval
            for approval in session.scalars(
                select(Approval).where(Approval.ticket_id.in_(evaluated_ticket_ids))
            ).all()
        }
        unapproved_writes = [
            execution
            for execution in all_executions
            if execution.ticket_id not in approvals
            or approvals[execution.ticket_id].state != "approved"
        ]

    engine.dispose()
    metrics = {
        name: _metric(sum(checks), len(checks)).model_dump()
        for name, checks in metric_checks.items()
    }
    # Keep legacy keys for API consumers but correct their documented meaning.
    metrics["rag_hit_at_k"] = metrics["rag_recall_at_k"]
    metrics["rag_recall_at_k"] = _metric(relevant_hits, relevant_total).model_dump()
    metrics["draft_workflow_success_rate"] = metrics["end_to_end_completion_rate"]
    metrics["overall_case_pass_rate"] = _metric(
        sum(result.passed for result in case_results), len(case_results)
    ).model_dump()
    metrics["unapproved_write_count"] = {
        "count": len(unapproved_writes),
        "target": 0,
        "passed": len(unapproved_writes) == 0,
    }
    category_counts = Counter(case.category for case in case_results)
    scenario_counts = Counter(case.scenario for case in case_results)
    report = {
        "suite": "ResolveAI Agent Evaluation",
        "version": "3.0",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "execution_mode": "live-deepseek-strict-isolated-sqlite" if live else "offline-demo-isolated-sqlite",
        "requested_cases": len(cases),
        "evaluated_cases": len(case_results),
        "complete": len(case_results) == len(cases),
        "not_run_case_ids": [case.id for case in cases[len(case_results):]],
        "passed_cases": sum(result.passed for result in case_results),
        "failed_cases": sum(not result.passed for result in case_results),
        "coverage": {
            "categories": dict(sorted(category_counts.items())),
            "scenarios": dict(sorted(scenario_counts.items())),
            "channels": dict(Counter(case.channel for case in cases[:len(case_results)])),
        },
        "metrics": metrics,
        "cases": [result.model_dump() for result in case_results],
        "limitations": [
            f"SQLite隔离环境、Embedding={EmbeddingService().profile}、模拟订单工具；非PostgreSQL/MCP在线联测",
            "工具选择由工作流代码驱动，不代表模型自主选择工具",
            "引用指标只验证原文可追溯性，不代表回复受到引用支持",
            "草稿流程达标不代表客户已确认解决；回复语义质量仍需人工复核",
            "未审批写操作为被动观察值，本轮不执行主动写接口攻击测试",
        ],
        "knowledge_snapshot": knowledge_snapshot,
        "embedding_profile": EmbeddingService().profile,
        "fixture_snapshot": fixture_snapshot(),
        "cohorts": {name: {"evaluated_cases": len(group), "passed_cases": sum(item.passed for item in group)}
                    for name, group in (("legacy_support", [item for item in case_results if item.observed["channel"] == "evaluation"]),
                                        ("commerce", [item for item in case_results if item.observed["channel"] == "web"]))},
    }
    if live is not None:
        from evaluation.live import public_config, summarize_calls

        metrics["live_model_case_rate"] = _metric(sum(live_checks), len(live_checks)).model_dump()
        report["model_config"] = public_config(get_settings())
        report["live_summary"] = summarize_calls(live)
        report["calls"] = live.calls
        report["manual_review_status"] = "pending"
        report["live_summary"]["rules_fallback_used"] = 0
        report["live_summary"]["fallback_prevented_cases"] = sum(
            result.observed.get("fallback_prevented", False) for result in case_results
        )
    return report


def render_markdown(report: dict[str, Any]) -> str:
    metric_labels = {
        "intent_accuracy": "意图识别准确率",
        "rag_recall_at_k": "RAG Recall@K",
        "citation_accuracy": "引用原文可追溯率（非语义支持率）",
        "tool_selection_accuracy": "工作流工具路由符合率（非模型选工具能力）",
        "tool_argument_accuracy": "工作流工具参数符合率",
        "high_risk_block_rate": "高风险拦截率",
        "end_to_end_completion_rate": "草稿流程达标率（非客户解决率）",
        "rag_hit_at_k": "RAG Hit@K（用例级至少命中一篇）",
        "overall_case_pass_rate": "整体用例通过率",
        "live_model_case_rate": "严格真实模型流程有效率",
        "business_behavior_accuracy": "业务行为断言通过率（非人工语义评分）",
        "low_risk_handoff_rate": "低风险误转人工率（越低越好）",
    }
    lines = [
        "# ResolveAI Agent 自动化评测报告",
        "",
        f"- 生成时间：`{report['generated_at']}`",
        f"- 执行模式：`{report['execution_mode']}`",
        f"- 用例结果：{report['passed_cases']} / {report['evaluated_cases']} 通过",
        f"- 完整执行：{'是' if report.get('complete', True) else '否；不得作为完整评测成绩'}",
        f"- 数据集 SHA256：`{report.get('dataset_sha256', '未提供')}`",
        "",
        "## 核心指标",
        "",
        "| 指标 | 结果 | 分子 / 分母 |",
        "| --- | ---: | ---: |",
    ]
    for key, label in metric_labels.items():
        if key not in report["metrics"]:
            continue
        metric = report["metrics"][key]
        lines.append(
            f"| {label} | {metric['display']} | {metric['numerator']} / {metric['denominator']} |"
        )
    write_metric = report["metrics"]["unapproved_write_count"]
    lines.append(
        f"| 未审批写操作次数 | {write_metric['count']} | 目标 = {write_metric['target']} |"
    )
    lines.extend(
        [
            "",
            "## 覆盖范围",
            "",
            "- 业务分类："
            + "、".join(
                f"{name} {count} 条"
                for name, count in report["coverage"]["categories"].items()
            ),
            "- 异常/对抗场景："
            + "、".join(
                f"{name} {count} 条"
                for name, count in report["coverage"]["scenarios"].items()
            ),
            "",
            "## 未通过用例",
            "",
        ]
    )
    failed_cases = [item for item in report["cases"] if not item["passed"]]
    if not failed_cases:
        lines.append("全部用例通过。")
    else:
        lines.extend(
            [
                "| 用例 | 分类 | 场景 | 未通过检查 |",
                "| --- | --- | --- | --- |",
            ]
        )
        for item in failed_cases:
            failed_checks = ", ".join(
                name for name, value in item["checks"].items() if value is False
            )
            if item.get("error"):
                failed_checks = f"{failed_checks}; {item['error']}".strip("; ")
            failed_checks = failed_checks.replace("|", "\\|").replace("\n", " ").replace("\r", " ")
            lines.append(
                f"| {item['id']} | {item['category']} | {item['scenario']} | {failed_checks} |"
            )
    if report.get("cohorts"):
        lines.extend(["", "## 分组结果", "", "| 分组 | 通过 / 已执行 |", "| --- | ---: |"])
        for name, cohort in report["cohorts"].items():
            lines.append(f"| {name} | {cohort['passed_cases']} / {cohort['evaluated_cases']} |")
    if "live_summary" in report:
        summary = report["live_summary"]
        lines.extend([
            "", "## 真实模型调用", "",
            f"- API调用：{summary['api_calls']} / {summary['max_api_calls']}；成功 {summary['successful_calls']}，失败 {summary['failed_calls']}",
            f"- 已知Token：{summary['total_tokens']}；缺少用量的调用：{summary['usage_unknown_calls']}",
            f"- 单次API尝试耗时：P50 {summary['p50_ms']} ms；P95 {summary['p95_ms']} ms",
            f"- 已知费用估算（USD）：{summary['known_estimated_cost_usd'] if summary['known_estimated_cost_usd'] is not None else '未知（不是免费）'}",
            f"- 费用记录完整：{'是' if summary['cost_complete'] else '否'}；{summary['cost_note']}",
            f"- 停止原因：{summary['abort_reason'] or '无'}",
            "- 回复语义质量：等待人工复核，尚未产生人工质量评分。",
            "", "## 模型与Prompt配置", "",
            "```json", json.dumps(report["model_config"], ensure_ascii=False, indent=2), "```",
        ])
    else:
        lines.extend(["", "> 说明：该报告使用 Demo 模型，不调用 DeepSeek。"])
    lines.extend(["", "## 评测边界", ""])
    lines.extend(f"- {note}" for note in report.get("limitations", []))
    lines.append("")
    return "\n".join(lines)


def write_reports(report: dict[str, Any], output_dir: Path) -> tuple[Path, Path]:
    output_dir.mkdir(parents=True, exist_ok=True)
    json_path = output_dir / "evaluation-latest.json"
    markdown_path = output_dir / "evaluation-latest.md"
    json_path.write_text(
        json.dumps(report, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    markdown_path.write_text(render_markdown(report), encoding="utf-8")
    if "live_summary" in report:
        review_path = output_dir / "manual-review.json"
        review_path.write_text(json.dumps([
            {
                "case_id": item["id"],
                "draft_response": item["observed"].get("draft_response"),
                "citations": item["observed"].get("citations", []),
                "facts_correct": None, "policy_correct": None,
                "citations_support_reply": None, "multi_turn_consistent": None,
                "reviewer": None, "notes": "",
            } for item in report["cases"]
        ], ensure_ascii=False, indent=2), encoding="utf-8")
    return json_path, markdown_path


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="ResolveAI评测：默认免费Demo；DeepSeek须显式允许付费调用")
    parser.add_argument("--provider", choices=("demo", "deepseek"), default="demo")
    parser.add_argument("--dry-run", action="store_true", help="只检查计划，不联网、不创建数据库")
    parser.add_argument("--allow-paid-api", action="store_true", help="明确允许DeepSeek付费调用")
    parser.add_argument("--env-file", type=Path, help="仅读取模型/RAG配置；默认backend/.env.eval或backend/.env")
    parser.add_argument("--smoke", action="store_true", help="退款/物流/支付/商品/账号各选一条，共5条")
    parser.add_argument("--max-api-calls", type=int, default=20, help="总API尝试次数上限（包括重试和质检重生成），默认20")
    parser.add_argument("--embedding-provider", choices=("auto", "local", "fastembed", "openai"))
    parser.add_argument("--embedding-cache-dir", type=Path)
    parser.add_argument("--holdout", action="store_true", help="运行20～200条、覆盖五类和八种场景的补充留出集")
    parser.add_argument("--prompt-version")
    parser.add_argument("--dataset", type=Path, default=DEFAULT_DATASET)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR)
    parser.add_argument("--limit", type=int, default=None, help="仅调试前 N 条用例")
    parser.add_argument("--case", action="append", dest="case_ids", help="仅运行指定用例 ID")
    parser.add_argument(
        "--fail-under",
        type=float,
        default=None,
        help="草稿流程达标率低于该百分比时返回非零退出码，例如 80",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    if args.max_api_calls < 1:
        raise ValueError("--max-api-calls 必须大于0")
    if args.provider == "deepseek" and not args.dry_run and not args.allow_paid_api:
        print("拒绝调用付费API：请先--dry-run检查计划；经费用确认后显式添加--allow-paid-api", file=sys.stderr)
        return 2
    if args.smoke and (args.limit is not None or args.case_ids):
        raise ValueError("--smoke 不能与 --limit/--case 混用")
    all_cases = load_cases(args.dataset)
    dataset_sha256 = sha256(args.dataset.read_bytes()).hexdigest()
    validate_dataset(all_cases, holdout=args.holdout)
    frozen_manifest = None
    if args.dataset.resolve() == DEFAULT_DATASET.resolve():
        from evaluation.fixtures import fixture_snapshot
        frozen_manifest = json.loads((EVALUATION_DIR / "commerce120.manifest.json").read_text(encoding="utf-8"))
        snapshot = fixture_snapshot()
        if (dataset_sha256 != frozen_manifest["dataset_sha256"]
            or snapshot["sha256"] != frozen_manifest["fixture_sha256"]
            or snapshot["implementation_sha256"] != frozen_manifest["fixture_implementation_sha256"]):
            raise ValueError("120条评测输入、标注或业务夹具与冻结版本不一致；请创建新版本，不得改预期追分")
    selected_cases = all_cases
    if args.smoke:
        selected_cases = [next(case for case in all_cases if case.category == category)
                          for category in ("refund", "logistics", "payment", "product", "account")]
    if args.case_ids:
        wanted = set(args.case_ids)
        selected_cases = [case for case in selected_cases if case.id in wanted]
        missing = wanted - {case.id for case in selected_cases}
        if missing:
            raise ValueError(f"找不到指定用例：{sorted(missing)}")
    if args.limit is not None:
        if args.limit < 1:
            raise ValueError("--limit 必须大于 0")
        selected_cases = selected_cases[: args.limit]

    settings = None
    live = None
    if args.provider == "deepseek" or args.embedding_provider is not None:
        from evaluation.live import LiveRun, default_env_file, evaluation_settings, public_config

        settings = evaluation_settings(
            args.env_file or default_env_file(), require_key=args.provider == "deepseek" and not args.dry_run,
            embedding_provider=args.embedding_provider, embedding_cache_dir=args.embedding_cache_dir,
            prompt_version=args.prompt_version,
        )
        if args.provider == "deepseek":
            live = LiveRun(args.max_api_calls)
    if args.dry_run:
        plan = {
            "dry_run": True, "api_calls": 0, "database_created": False,
            "provider": args.provider, "strict_live": args.provider == "deepseek",
            "selected_cases": len(selected_cases), "case_ids": [case.id for case in selected_cases],
            "dataset_sha256": dataset_sha256,
            "categories": dict(Counter(case.category for case in selected_cases)),
            "max_api_calls": args.max_api_calls if live else 0,
            "key_configured": bool(settings and settings.deepseek_api_key),
            "isolation": "independent SQLite; fixed commerce fixtures; no external MCP/Redis/OTel",
        }
        if settings is not None:
            plan["model_config"] = public_config(settings)
        print(json.dumps(plan, ensure_ascii=False, indent=2))
        return 0

    run_id = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ") + "-" + uuid4().hex[:8]
    output_dir = args.output_dir / "live" / run_id if live else args.output_dir
    database_path = output_dir / f".evaluation-{uuid4().hex}.db"
    _configure_isolated_environment(database_path, settings)
    output_dir.mkdir(parents=True, exist_ok=True)
    def progress(update):
        (output_dir / "progress.json").write_text(json.dumps({"run_id": run_id, **update}), encoding="utf-8")
    try:
        report = asyncio.run(run_suite(selected_cases, live=live, progress=progress))
    finally:
        # Only remove the unique database created by this invocation, never business data.
        if "app.core.database" in sys.modules:
            sys.modules["app.core.database"].engine.dispose()
        database_path.unlink(missing_ok=True)
    report["run_id"] = run_id
    report["dataset_sha256"] = dataset_sha256
    report["dataset_cases"] = len(all_cases)
    report["suite_kind"] = "holdout" if args.holdout else "regression"
    report["frozen_manifest"] = frozen_manifest
    archive_dir = output_dir / "runs" / run_id
    write_reports(report, archive_dir)
    json_path, markdown_path = write_reports(report, output_dir)
    print(f"评测完成：{report['passed_cases']}/{report['evaluated_cases']} 条用例通过")
    for name, metric in report["metrics"].items():
        if name == "unapproved_write_count":
            print(f"- {name}: {metric['count']}")
        else:
            print(f"- {name}: {metric['display']}")
    print(f"JSON：{json_path.resolve()}")
    print(f"Markdown：{markdown_path.resolve()}")

    if live and (live.abort_reason or not report["complete"]):
        print(f"评测未完整执行：{live.abort_reason or '未知错误'}", file=sys.stderr)
        return 1
    if live and report["metrics"]["live_model_case_rate"]["score"] != 1.0:
        print("存在真实模型失败/无效响应，已按失败计分，未使用Demo结果充数", file=sys.stderr)
        return 1

    if args.fail_under is not None:
        score = report["metrics"]["end_to_end_completion_rate"]["score"] or 0
        if score * 100 < args.fail_under:
            print(
                f"草稿流程达标率 {score:.2%} 低于门槛 {args.fail_under:.2f}%",
                file=sys.stderr,
            )
            return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
