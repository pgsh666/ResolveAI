import json
from collections.abc import AsyncIterator, Iterator, Sequence
from typing import Any

from langchain_core.runnables import RunnableConfig
from langgraph.checkpoint.base import (
    WRITES_IDX_MAP,
    BaseCheckpointSaver,
    Checkpoint,
    CheckpointMetadata,
    CheckpointTuple,
    get_checkpoint_id,
    get_checkpoint_metadata,
)
from sqlalchemy import delete, select

from app.core.database import SessionLocal
from app.models import (
    LangGraphCheckpoint,
    LangGraphCheckpointWrite,
    WorkflowCheckpoint,
    utc_now,
)


STATE_KEYS = {
    "ticket",
    "conversation",
    "triage",
    "customer_context",
    "citations",
    "rag_status",
    "policy",
    "resolution",
    "quality",
    "quality_feedback",
    "generation_attempt",
    "gate",
    "model_trace",
    "audit",
    "errors",
    "failures",
}


class SQLAlchemyCheckpointSaver(BaseCheckpointSaver):
    """LangGraph checkpointer shared by PostgreSQL and the SQLite test mode."""

    def get_tuple(self, config: RunnableConfig) -> CheckpointTuple | None:
        configurable = config["configurable"]
        thread_id = str(configurable["thread_id"])
        checkpoint_ns = str(configurable.get("checkpoint_ns", ""))
        checkpoint_id = get_checkpoint_id(config)

        with SessionLocal() as session:
            statement = select(LangGraphCheckpoint).where(
                LangGraphCheckpoint.thread_id == thread_id,
                LangGraphCheckpoint.checkpoint_ns == checkpoint_ns,
            )
            if checkpoint_id:
                statement = statement.where(
                    LangGraphCheckpoint.checkpoint_id == checkpoint_id
                )
            else:
                statement = statement.order_by(
                    LangGraphCheckpoint.checkpoint_id.desc()
                ).limit(1)
            row = session.scalar(statement)
            if row is None:
                return None
            return self._to_tuple(session, row)

    def list(
        self,
        config: RunnableConfig | None,
        *,
        filter: dict[str, Any] | None = None,
        before: RunnableConfig | None = None,
        limit: int | None = None,
    ) -> Iterator[CheckpointTuple]:
        with SessionLocal() as session:
            statement = select(LangGraphCheckpoint)
            if config:
                configurable = config["configurable"]
                statement = statement.where(
                    LangGraphCheckpoint.thread_id == str(configurable["thread_id"]),
                    LangGraphCheckpoint.checkpoint_ns
                    == str(configurable.get("checkpoint_ns", "")),
                )
            if before and (before_id := get_checkpoint_id(before)):
                statement = statement.where(
                    LangGraphCheckpoint.checkpoint_id < before_id
                )
            statement = statement.order_by(LangGraphCheckpoint.checkpoint_id.desc())
            if limit is not None:
                statement = statement.limit(limit)
            rows = list(session.scalars(statement).all())
            for row in rows:
                item = self._to_tuple(session, row)
                if filter and any(item.metadata.get(key) != value for key, value in filter.items()):
                    continue
                yield item

    def put(
        self,
        config: RunnableConfig,
        checkpoint: Checkpoint,
        metadata: CheckpointMetadata,
        new_versions: dict[str, Any],
    ) -> RunnableConfig:
        del new_versions
        configurable = config["configurable"]
        thread_id = str(configurable["thread_id"])
        checkpoint_ns = str(configurable.get("checkpoint_ns", ""))
        checkpoint_id = str(checkpoint["id"])
        parent_checkpoint_id = configurable.get("checkpoint_id")
        checkpoint_type, checkpoint_blob = self.serde.dumps_typed(checkpoint)
        metadata_type, metadata_blob = self.serde.dumps_typed(
            get_checkpoint_metadata(config, metadata)
        )

        with SessionLocal() as session:
            row = session.scalar(
                select(LangGraphCheckpoint).where(
                    LangGraphCheckpoint.thread_id == thread_id,
                    LangGraphCheckpoint.checkpoint_ns == checkpoint_ns,
                    LangGraphCheckpoint.checkpoint_id == checkpoint_id,
                )
            )
            if row is None:
                row = LangGraphCheckpoint(
                    thread_id=thread_id,
                    checkpoint_ns=checkpoint_ns,
                    checkpoint_id=checkpoint_id,
                    parent_checkpoint_id=(
                        str(parent_checkpoint_id) if parent_checkpoint_id else None
                    ),
                    checkpoint_type=checkpoint_type,
                    checkpoint_blob=checkpoint_blob,
                    metadata_type=metadata_type,
                    metadata_blob=metadata_blob,
                )
                session.add(row)
            else:
                row.parent_checkpoint_id = (
                    str(parent_checkpoint_id) if parent_checkpoint_id else None
                )
                row.checkpoint_type = checkpoint_type
                row.checkpoint_blob = checkpoint_blob
                row.metadata_type = metadata_type
                row.metadata_blob = metadata_blob

            self._update_workflow_summary(
                session,
                thread_id=thread_id,
                checkpoint_id=checkpoint_id,
                checkpoint=checkpoint,
                metadata=metadata,
            )
            session.commit()

        return {
            "configurable": {
                "thread_id": thread_id,
                "checkpoint_ns": checkpoint_ns,
                "checkpoint_id": checkpoint_id,
            }
        }

    def put_writes(
        self,
        config: RunnableConfig,
        writes: Sequence[tuple[str, Any]],
        task_id: str,
        task_path: str = "",
    ) -> None:
        configurable = config["configurable"]
        thread_id = str(configurable["thread_id"])
        checkpoint_ns = str(configurable.get("checkpoint_ns", ""))
        checkpoint_id = str(configurable["checkpoint_id"])
        with SessionLocal() as session:
            for index, (channel, value) in enumerate(writes):
                write_index = WRITES_IDX_MAP.get(channel, index)
                existing = session.scalar(
                    select(LangGraphCheckpointWrite).where(
                        LangGraphCheckpointWrite.thread_id == thread_id,
                        LangGraphCheckpointWrite.checkpoint_ns == checkpoint_ns,
                        LangGraphCheckpointWrite.checkpoint_id == checkpoint_id,
                        LangGraphCheckpointWrite.task_id == task_id,
                        LangGraphCheckpointWrite.write_index == write_index,
                    )
                )
                if existing is not None and write_index >= 0:
                    continue
                value_type, value_blob = self.serde.dumps_typed(value)
                if existing is None:
                    session.add(
                        LangGraphCheckpointWrite(
                            thread_id=thread_id,
                            checkpoint_ns=checkpoint_ns,
                            checkpoint_id=checkpoint_id,
                            task_id=task_id,
                            task_path=task_path,
                            write_index=write_index,
                            channel=channel,
                            value_type=value_type,
                            value_blob=value_blob,
                        )
                    )
                else:
                    existing.task_path = task_path
                    existing.channel = channel
                    existing.value_type = value_type
                    existing.value_blob = value_blob
            session.commit()

    def delete_thread(self, thread_id: str) -> None:
        with SessionLocal() as session:
            session.execute(
                delete(LangGraphCheckpointWrite).where(
                    LangGraphCheckpointWrite.thread_id == thread_id
                )
            )
            session.execute(
                delete(LangGraphCheckpoint).where(
                    LangGraphCheckpoint.thread_id == thread_id
                )
            )
            session.commit()

    async def aget_tuple(self, config: RunnableConfig) -> CheckpointTuple | None:
        return self.get_tuple(config)

    async def alist(
        self,
        config: RunnableConfig | None,
        *,
        filter: dict[str, Any] | None = None,
        before: RunnableConfig | None = None,
        limit: int | None = None,
    ) -> AsyncIterator[CheckpointTuple]:
        for item in self.list(config, filter=filter, before=before, limit=limit):
            yield item

    async def aput(
        self,
        config: RunnableConfig,
        checkpoint: Checkpoint,
        metadata: CheckpointMetadata,
        new_versions: dict[str, Any],
    ) -> RunnableConfig:
        return self.put(config, checkpoint, metadata, new_versions)

    async def aput_writes(
        self,
        config: RunnableConfig,
        writes: Sequence[tuple[str, Any]],
        task_id: str,
        task_path: str = "",
    ) -> None:
        self.put_writes(config, writes, task_id, task_path)

    async def adelete_thread(self, thread_id: str) -> None:
        self.delete_thread(thread_id)

    def _to_tuple(
        self,
        session,
        row: LangGraphCheckpoint,
    ) -> CheckpointTuple:
        checkpoint = self.serde.loads_typed(
            (row.checkpoint_type, bytes(row.checkpoint_blob))
        )
        metadata = self.serde.loads_typed(
            (row.metadata_type, bytes(row.metadata_blob))
        )
        write_rows = list(
            session.scalars(
                select(LangGraphCheckpointWrite)
                .where(
                    LangGraphCheckpointWrite.thread_id == row.thread_id,
                    LangGraphCheckpointWrite.checkpoint_ns == row.checkpoint_ns,
                    LangGraphCheckpointWrite.checkpoint_id == row.checkpoint_id,
                )
                .order_by(LangGraphCheckpointWrite.write_index.asc())
            ).all()
        )
        pending_writes = [
            (
                item.task_id,
                item.channel,
                self.serde.loads_typed((item.value_type, bytes(item.value_blob))),
            )
            for item in write_rows
        ]
        item_config: RunnableConfig = {
            "configurable": {
                "thread_id": row.thread_id,
                "checkpoint_ns": row.checkpoint_ns,
                "checkpoint_id": row.checkpoint_id,
            }
        }
        parent_config = None
        if row.parent_checkpoint_id:
            parent_config = {
                "configurable": {
                    "thread_id": row.thread_id,
                    "checkpoint_ns": row.checkpoint_ns,
                    "checkpoint_id": row.parent_checkpoint_id,
                }
            }
        return CheckpointTuple(
            config=item_config,
            checkpoint=checkpoint,
            metadata=metadata,
            parent_config=parent_config,
            pending_writes=pending_writes,
        )

    @staticmethod
    def _update_workflow_summary(
        session,
        *,
        thread_id: str,
        checkpoint_id: str,
        checkpoint: Checkpoint,
        metadata: CheckpointMetadata,
    ) -> None:
        values = checkpoint.get("channel_values", {})
        state = {
            key: _json_safe(value)
            for key, value in values.items()
            if key in STATE_KEYS
        }
        ticket = state.get("ticket") or {}
        ticket_id = ticket.get("id")
        workflow = session.scalar(
            select(WorkflowCheckpoint).where(
                WorkflowCheckpoint.thread_id == thread_id
            )
        )
        if workflow is None and ticket_id:
            workflow = session.scalar(
                select(WorkflowCheckpoint).where(
                    WorkflowCheckpoint.ticket_id == ticket_id
                )
            )
        if workflow is None:
            return

        writes = metadata.get("writes") if isinstance(metadata, dict) else None
        current_node = workflow.current_node
        if isinstance(writes, dict) and writes:
            node_names = [str(name) for name in writes if not str(name).startswith("__")]
            if node_names:
                current_node = "+".join(node_names)

        workflow.thread_id = thread_id
        workflow.checkpoint_id = checkpoint_id
        workflow.current_node = current_node
        workflow.next_node = _next_node(current_node)
        workflow.state = state
        if state.get("customer_context"):
            workflow.tool_results = [
                {
                    "tool_name": "get_order_context",
                    "result": state["customer_context"],
                }
            ]
        if state.get("conversation"):
            workflow.conversation = state["conversation"]
        workflow.version += 1
        workflow.updated_at = utc_now()


def _next_node(current_node: str) -> str | None:
    mapping = {
        "START": "triage_agent",
        "triage_agent": "knowledge_agent+customer_agent",
        "knowledge_agent": "customer_agent",
        "customer_agent": "knowledge_agent",
        "knowledge_agent+customer_agent": "policy_agent",
        "customer_agent+knowledge_agent": "policy_agent",
        "policy_agent": "resolution_agent",
        "resolution_agent": "quality_agent",
        "quality_agent": "resolution_agent|approval_gate",
        "approval_gate": None,
    }
    return mapping.get(current_node)


def _json_safe(value: Any) -> Any:
    return json.loads(json.dumps(value, ensure_ascii=False, default=str))


__all__ = ["SQLAlchemyCheckpointSaver"]
