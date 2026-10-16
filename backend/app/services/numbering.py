"""Atomic daily numbering in the shop's China business timezone."""
from datetime import timedelta, timezone

from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert

from app.models import DailySequence, utc_now

BUSINESS_TZ = timezone(timedelta(hours=8))


def next_number(session, namespace="ticket", at=None):
    day = (at or utc_now()).astimezone(BUSINESS_TZ).strftime("%y-%m-%d")
    key = f"{namespace}:{day}"
    insert = pg_insert if session.bind.dialect.name == "postgresql" else sqlite_insert
    stmt = insert(DailySequence).values(key=key, value=1)
    number = session.scalar(stmt.on_conflict_do_update(
        index_elements=["key"], set_={"value": DailySequence.value + 1},
    ).returning(DailySequence.value))
    return f"{day}-{number}" if namespace == "ticket" else f"ORD-{day}-{number}"
