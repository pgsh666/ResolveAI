from contextvars import ContextVar
from datetime import timezone
from functools import wraps
from time import perf_counter
import logging

from sqlalchemy.orm import Session

from app.models import ModelUsage, WorkflowRun, utc_now

CURRENT_RUN = ContextVar("resolveai_workflow_run", default=None)


def utc(value):
    return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value.astimezone(timezone.utc)


def persist_usage(bind, ticket_id, metadata):
    with Session(bind=bind) as session:
        event_id = metadata["event_id"]
        if session.get(ModelUsage, event_id):
            return
        session.add(ModelUsage(id=event_id, ticket_id=ticket_id, workflow_run_id=CURRENT_RUN.get(),
                              stage=metadata["stage"], model=str(metadata.get("model") or "unknown"), usage=metadata))
        session.commit()


def meter_workflow(operation):
    def decorate(function):
        @wraps(function)
        async def wrapped(self, ticket, *args, **kwargs):
            started_at = utc_now()
            submitted_at = utc(self.submitted_at) if getattr(self, "submitted_at", None) else started_at
            row = WorkflowRun(ticket_id=ticket.id, operation=operation, task_id=getattr(self, "task_id", None),
                              submitted_at=submitted_at, started_at=started_at,
                              queue_ms=max(0.0, (started_at - submitted_at).total_seconds() * 1000))
            self.session.add(row)
            self.session.commit()
            self.last_run_id = row.id
            token = CURRENT_RUN.set(row.id)
            timer = perf_counter()
            try:
                result = await function(self, ticket, *args, **kwargs)
                row.status = "waiting_approval" if result.requires_approval else "completed"
                return result
            except Exception:
                self.session.rollback()
                row = self.session.get(WorkflowRun, row.id)
                if row:
                    row.status = "failed"
                raise
            finally:
                try:
                    if row:
                        row.finished_at = utc_now()
                        row.processing_ms = round((perf_counter() - timer) * 1000, 3)
                        row.end_to_end_ms = round(max(0.0, (row.finished_at - submitted_at).total_seconds() * 1000), 3)
                        self.session.add(row)
                        self.session.commit()
                except Exception:
                    self.session.rollback()
                    logging.getLogger(__name__).error("Workflow metering persistence failed; original business outcome retained")
                finally:
                    CURRENT_RUN.reset(token)
        return wrapped
    return decorate
