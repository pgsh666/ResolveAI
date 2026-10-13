from datetime import date, datetime, timezone

from app.services.analytics import build_activity_trend


def test_activity_trend_uses_real_dates_and_distinct_tickets():
    rows = [
        (datetime(2026, 9, 16, 1, tzinfo=timezone.utc), "分诊 Agent", "ticket-a"),
        (datetime(2026, 9, 16, 2, tzinfo=timezone.utc), "知识检索 Agent", "ticket-a"),
        (datetime(2026, 9, 16, 3, tzinfo=timezone.utc), "人工审批", "ticket-a"),
        (datetime(2026, 9, 16, 4, tzinfo=timezone.utc), "分诊 Agent", "ticket-b"),
        (datetime(2026, 9, 17, 1, tzinfo=timezone.utc), "分诊 Agent", "ticket-c"),
    ]

    trend = build_activity_trend(rows, today=date(2026, 9, 17))

    assert [item["day"] for item in trend] == [
        "09/11",
        "09/12",
        "09/13",
        "09/14",
        "09/15",
        "09/16",
        "今天",
    ]
    assert all(item["automated"] == item["manual"] == 0 for item in trend[:5])
    assert trend[5] == {"day": "09/16", "automated": 1, "manual": 1}
    assert trend[6] == {"day": "今天", "automated": 1, "manual": 0}
