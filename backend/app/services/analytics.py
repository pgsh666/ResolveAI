from collections import defaultdict
from datetime import date, datetime, timedelta, timezone
from typing import Iterable


SHANGHAI_TZ = timezone(timedelta(hours=8))
HUMAN_AGENTS = {"人工审批", "客服坐席"}


def _local_date(value: datetime) -> date:
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(SHANGHAI_TZ).date()


def build_activity_trend(
    rows: Iterable[tuple[datetime, str, str]],
    *,
    today: date | None = None,
) -> list[dict[str, int | str]]:
    """Count distinct handled tickets per local day, split by human involvement."""

    current_day = today or datetime.now(SHANGHAI_TZ).date()
    days = [current_day - timedelta(days=offset) for offset in range(6, -1, -1)]
    activity: dict[date, dict[str, set[str]]] = defaultdict(lambda: defaultdict(set))

    for created_at, agent, ticket_id in rows:
        event_day = _local_date(created_at)
        if event_day in days:
            activity[event_day][ticket_id].add(agent)

    trend: list[dict[str, int | str]] = []
    for day in days:
        ticket_agents = activity.get(day, {})
        manual = sum(bool(agents & HUMAN_AGENTS) for agents in ticket_agents.values())
        automated = len(ticket_agents) - manual
        trend.append(
            {
                "day": "今天" if day == current_day else day.strftime("%m/%d"),
                "automated": automated,
                "manual": manual,
            }
        )
    return trend
