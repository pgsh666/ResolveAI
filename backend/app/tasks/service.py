from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from redis import Redis
from redis.exceptions import RedisError
from rq import Queue, Worker
from rq.exceptions import NoSuchJobError
from rq.job import Job

from app.core.config import Settings, get_settings
from app.services.security import redact_text


TERMINAL_TASK_STATUSES = {"finished", "failed", "stopped", "canceled"}


class TaskQueueUnavailableError(RuntimeError):
    """Raised when asynchronous jobs are disabled or Redis cannot be reached."""


class BackgroundTaskNotFoundError(LookupError):
    """Raised when an RQ job ID is unknown or has expired."""


def _iso_or_none(value: datetime | None) -> str | None:
    return value.isoformat() if value else None


def _status_value(value: Any) -> str:
    return str(getattr(value, "value", value))


def _safe_failure(exc_info: str | None, meta_error: str | None = None) -> str | None:
    if meta_error:
        return redact_text(meta_error)[:1000]
    if not exc_info:
        return None
    lines = [line.strip() for line in exc_info.splitlines() if line.strip()]
    return redact_text(lines[-1] if lines else "后台任务执行失败")[:1000]


class TaskQueueService:
    def __init__(self, settings: Settings | None = None) -> None:
        self.settings = settings or get_settings()
        self.connection = Redis.from_url(
            self.settings.redis_url,
            socket_connect_timeout=2,
            socket_timeout=3,
            health_check_interval=30,
        )
        self.queue = Queue(self.settings.task_queue_name, connection=self.connection)

    def ensure_available(self) -> None:
        if not self.settings.async_tasks_enabled:
            raise TaskQueueUnavailableError("后台任务未启用，请设置 ASYNC_TASKS_ENABLED=true")
        try:
            self.connection.ping()
        except RedisError as error:
            raise TaskQueueUnavailableError("Redis 不可用，无法创建后台任务") from error

    def health(self) -> dict[str, Any]:
        self.ensure_available()
        return {
            "redis": "ok",
            "queue": self.settings.task_queue_name,
            "workers": Worker.count(queue=self.queue),
            "queued_jobs": self.queue.count,
        }

    def enqueue_agent_analysis(self, ticket_id: str) -> dict[str, Any]:
        return self._enqueue(
            "app.tasks.jobs.analyze_ticket_job",
            args=(ticket_id,),
            task_type="agent_analysis",
            message="Agent 分析已进入队列",
            description=f"Analyze ticket {ticket_id}",
        )

    def enqueue_knowledge_import(self, documents: list[dict[str, Any]], workspace_id: str | None = None) -> dict[str, Any]:
        return self._enqueue(
            "app.tasks.jobs.import_knowledge_job",
            args=(documents, workspace_id),
            task_type="knowledge_import",
            message=f"{len(documents)} 篇知识文档已进入队列",
            description=f"Import {len(documents)} knowledge documents",
        )

    def enqueue_evaluation(self, limit: int | None) -> dict[str, Any]:
        return self._enqueue(
            "app.tasks.jobs.run_evaluation_job",
            args=(limit,),
            task_type="batch_evaluation",
            message="Agent 批量评测已进入队列",
            description="Run ResolveAI batch evaluation",
            timeout=self.settings.task_evaluation_timeout_seconds,
        )

    def _enqueue(
        self,
        function: str,
        *,
        args: tuple[Any, ...],
        task_type: str,
        message: str,
        description: str,
        timeout: int | None = None,
    ) -> dict[str, Any]:
        self.ensure_available()
        now = datetime.now(timezone.utc).isoformat()
        try:
            job = self.queue.enqueue_call(
                function,
                args=args,
                timeout=timeout or self.settings.task_default_timeout_seconds,
                result_ttl=self.settings.task_result_ttl_seconds,
                failure_ttl=self.settings.task_failure_ttl_seconds,
                description=description,
                meta={
                    "task_type": task_type,
                    "progress": 0,
                    "stage": "queued",
                    "message": message,
                    "updated_at": now,
                },
            )
        except RedisError as error:
            raise TaskQueueUnavailableError("Redis 写入失败，后台任务未创建") from error
        return self.snapshot(job.id)

    def snapshot(self, task_id: str) -> dict[str, Any]:
        try:
            job = Job.fetch(task_id, connection=self.connection)
            raw_status = job.get_status(refresh=True)
            meta = job.get_meta(refresh=True) or {}
        except NoSuchJobError as error:
            raise BackgroundTaskNotFoundError(task_id) from error
        except RedisError as error:
            raise TaskQueueUnavailableError("Redis 不可用，无法读取后台任务") from error

        status = _status_value(raw_status)
        result = None
        if status == "finished":
            try:
                result = job.return_value(refresh=True)
            except AttributeError:  # RQ 1.x compatibility
                result = job.result
        if result is not None and not isinstance(result, dict):
            result = {"value": result}

        progress = int(meta.get("progress", 100 if status == "finished" else 0))
        if status == "finished":
            progress = 100
        message = str(meta.get("message") or status)
        if status == "failed" and message == "started":
            message = "后台任务执行失败"

        return {
            "id": job.id,
            "task_type": meta.get("task_type", "agent_analysis"),
            "status": status,
            "progress": max(0, min(progress, 100)),
            "stage": str(meta.get("stage") or status),
            "message": message,
            "result": result,
            "error": _safe_failure(job.exc_info, meta.get("error")) if status == "failed" else None,
            "created_at": _iso_or_none(job.created_at or job.enqueued_at),
            "started_at": _iso_or_none(job.started_at),
            "updated_at": meta.get("updated_at") or _iso_or_none(job.last_heartbeat),
            "finished_at": _iso_or_none(job.ended_at),
        }


def update_current_task(
    progress: int,
    stage: str,
    message: str,
    *,
    error: str | None = None,
) -> None:
    """Persist progress on the current RQ job; no-op for direct unit-test calls."""
    from rq import get_current_job

    job = get_current_job()
    if job is None:
        return
    job.meta.update(
        {
            "progress": max(0, min(int(progress), 100)),
            "stage": stage,
            "message": message,
            "updated_at": datetime.now(timezone.utc).isoformat(),
        }
    )
    if error:
        job.meta["error"] = redact_text(error)[:1000]
    job.save_meta()
