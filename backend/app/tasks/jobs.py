from __future__ import annotations

import asyncio
import json
import subprocess
import sys
from pathlib import Path
from typing import Any

from rq import get_current_job
from sqlalchemy import select

from app.core.config import get_settings
from app.core.database import SessionLocal
from app.core.observability import flush_tracing, get_tracer
from app.models import KnowledgeAccess, KnowledgeDocument, Ticket
from app.services.embeddings import EmbeddingService
from app.services.security import redact_text
from app.services.retrieval import invalidate_retrieval_cache
from app.services.workflow import WorkflowService
from app.tasks.service import update_current_task


def _fail(error: Exception) -> None:
    update_current_task(
        100,
        "failed",
        "后台任务执行失败",
        error=f"{type(error).__name__}: {error}",
    )


def analyze_ticket_job(ticket_id: str) -> dict[str, Any]:
    update_current_task(5, "loading_ticket", "正在读取工单与会话上下文")
    try:
        with get_tracer().start_as_current_span("resolveai.task.agent_analysis") as span:
            span.set_attribute("resolveai.task.type", "agent_analysis")
            span.set_attribute("resolveai.ticket.id", ticket_id)
            with SessionLocal() as session:
                ticket = session.get(Ticket, ticket_id)
                if ticket is None:
                    raise LookupError("工单不存在")
                update_current_task(15, "workflow", "7 个 Agent 节点正在协作分析")
                workflow = WorkflowService(session)
                job = get_current_job()
                if job:
                    workflow.submitted_at = job.enqueued_at or job.created_at
                    workflow.task_id = job.id
                detail = asyncio.run(workflow.analyze(ticket))
                update_current_task(95, "persisting", "正在保存工作流结果与审计记录")
                result = {
                    "ticket_id": detail.id,
                    "ticket_no": detail.ticket_no,
                    "status": detail.status,
                    "requires_approval": detail.requires_approval,
                }
                observability = (detail.assessment or {}).get("observability") or {}
                if observability.get("trace_id"):
                    result["trace_id"] = observability["trace_id"]
                    result["trace_url"] = observability.get("trace_url")
                update_current_task(100, "completed", "Agent 分析已完成")
                return result
    except Exception as error:
        _fail(error)
        raise
    finally:
        flush_tracing()


def import_knowledge_job(documents: list[dict[str, Any]], workspace_id: str | None = None) -> dict[str, Any]:
    total = len(documents)
    update_current_task(3, "validating", f"正在校验 {total} 篇知识文档")
    imported = 0
    updated = 0
    document_ids: list[str] = []
    try:
        with get_tracer().start_as_current_span("resolveai.task.knowledge_import") as span:
            span.set_attribute("resolveai.task.type", "knowledge_import")
            span.set_attribute("resolveai.knowledge.document_count", total)
            embeddings = EmbeddingService()
            with SessionLocal() as session:
                for index, item in enumerate(documents, start=1):
                    title = redact_text(str(item["title"]).strip())
                    source = str(item["source"]).strip()
                    content = redact_text(str(item["content"]).strip())
                    category = str(item.get("category") or "general").strip()
                    update_current_task(
                        5 + int((index - 1) / max(total, 1) * 85),
                        "embedding",
                        f"正在生成向量：{title}",
                    )
                    embedding = embeddings.legacy_embed(f"{title}\n{content}")
                    statement = select(KnowledgeDocument).where(KnowledgeDocument.source == source)
                    if workspace_id:
                        from sqlalchemy import or_
                        owned = KnowledgeDocument.id.in_(select(KnowledgeAccess.document_id).where(KnowledgeAccess.workspace_id == workspace_id))
                        shared = ~KnowledgeDocument.id.in_(select(KnowledgeAccess.document_id)) if workspace_id == "default" else False
                        statement = statement.where(or_(owned, shared))
                    document = session.scalar(statement.limit(1))
                    if document is None:
                        document = KnowledgeDocument(
                            title=title,
                            source=source,
                            content=content,
                            category=category,
                            embedding=embedding,
                        )
                        session.add(document)
                        session.flush()
                        if workspace_id:
                            session.add(KnowledgeAccess(document_id=document.id, workspace_id=workspace_id))
                        imported += 1
                    else:
                        document.title = title
                        document.content = content
                        document.category = category
                        document.embedding = embedding
                        updated += 1
                    session.flush()
                    document_ids.append(document.id)
                update_current_task(94, "persisting", "正在提交知识文档与向量")
                session.commit()
                if embeddings.provider == "fastembed":
                    from app.services.knowledge_index import ensure_semantic_index

                    ensure_semantic_index(session, embeddings)
            invalidate_retrieval_cache()
            update_current_task(100, "completed", "知识库导入已完成")
            return {
                "total": total,
                "imported": imported,
                "updated": updated,
                "document_ids": document_ids,
            }
    except Exception as error:
        _fail(error)
        raise
    finally:
        flush_tracing()


def run_evaluation_job(limit: int | None = None) -> dict[str, Any]:
    settings = get_settings()
    current_job = get_current_job()
    task_id = current_job.id if current_job else "direct"
    output_dir = Path(settings.task_evaluation_output_dir).resolve() / task_id
    output_dir.mkdir(parents=True, exist_ok=True)
    command = [
        sys.executable,
        "-m",
        "evaluation.run",
        "--output-dir",
        str(output_dir),
    ]
    if limit is not None:
        command.extend(["--limit", str(limit)])

    update_current_task(5, "preparing", "正在准备隔离评测环境")
    try:
        with get_tracer().start_as_current_span("resolveai.task.batch_evaluation") as span:
            span.set_attribute("resolveai.task.type", "batch_evaluation")
            span.set_attribute("resolveai.evaluation.case_limit", limit or 60)
            update_current_task(15, "evaluating", "正在运行 Agent 批量评测")
            completed = subprocess.run(
                command,
                cwd=Path(__file__).resolve().parents[2],
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=settings.task_evaluation_timeout_seconds - 10,
                check=False,
            )
            if completed.returncode != 0:
                detail = (completed.stderr or completed.stdout or "评测进程异常退出").strip()
                raise RuntimeError(detail[-1500:])

            update_current_task(92, "reporting", "正在生成 JSON 与 Markdown 报告")
            json_path = output_dir / "evaluation-latest.json"
            report = json.loads(json_path.read_text(encoding="utf-8"))
            result = {
                "evaluated_cases": report["evaluated_cases"],
                "passed_cases": report["passed_cases"],
                "failed_cases": report["failed_cases"],
                "metrics": report["metrics"],
                "json_report": str(json_path),
                "markdown_report": str(output_dir / "evaluation-latest.md"),
            }
            update_current_task(100, "completed", "Agent 批量评测已完成")
            return result
    except Exception as error:
        _fail(error)
        raise
    finally:
        flush_tracing()
