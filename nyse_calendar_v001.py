"""Pinned NYSE 2026 cash-equity regular-session calendar.

Official published holiday/early-close reference:
https://www.nyse.com/trade/hours-calendars

Covered evaluation dates: 2026-10-22 through 2026-12-31.
Does not monitor unscheduled exchange closures.
No network requests, AI calls, or trading operations.
"""

from datetime import date, datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo

ET = ZoneInfo("America/New_York")

FIRST = date(2026, 10, 22)
LAST = date(2026, 12, 31)

NYSE_HOLIDAYS_2026 = frozenset({
    date(2026, 1, 1),
    date(2026, 1, 19),
    date(2026, 2, 16),
    date(2026, 4, 3),
    date(2026, 5, 25),
    date(2026, 6, 19),
    date(2026, 7, 3),
    date(2026, 9, 7),
    date(2026, 11, 26),
    date(2026, 12, 25)
})

NYSE_EARLY_CLOSES_2026 = frozenset({
    date(2026, 11, 27),
    date(2026, 12, 24)
})


def require(ok, reason):
    if not ok:
        raise ValueError("NYSE CALENDAR STOPPED: " + reason)


def scheduled_session(day):
    """Return the published schedule, not a live status."""
    require(
        isinstance(day, date)
        and not isinstance(day, datetime),
        "Date required"
    )

    require(
        FIRST <= day <= LAST,
        "Date outside pinned 2026 coverage; "
        "new official schedule verification required"
    )

    if day.weekday() >= 5:
        kind, closing = "CLOSED_WEEKEND", None
    elif day in NYSE_HOLIDAYS_2026:
        kind, closing = "CLOSED_HOLIDAY", None
    elif day in NYSE_EARLY_CLOSES_2026:
        kind, closing = "EARLY_CLOSE", time(13, 0)
    else:
        kind, closing = "STANDARD", time(16, 0)

    if closing is None:
        return {
            "date": day.isoformat(),
            "kind": kind,
            "expected_bars": 0,
            "open_utc": None,
            "close_utc": None
        }

    opening = datetime.combine(
        day, time(9, 30), tzinfo=ET
    )
    ending = datetime.combine(
        day, closing, tzinfo=ET
    )

    return {
        "date": day.isoformat(),
        "kind": kind,
        "expected_bars": int(
            (ending - opening).total_seconds() // 300
        ),
        "open_utc": opening.astimezone(
            timezone.utc
        ),
        "close_utc": ending.astimezone(
            timezone.utc
        )
    }


def validate_scheduled_timestamps(day, timestamps):
    """Schedule-only check; OHLCV is validated separately."""
    schedule = scheduled_session(day)

    require(
        schedule["expected_bars"] > 0,
        "No scheduled equity session on this date"
    )

    require(
        len(timestamps) == schedule["expected_bars"],
        "Candle count does not match official schedule"
    )

    for i, value in enumerate(timestamps):
        require(
            isinstance(value, datetime)
            and value.tzinfo is not None
            and value.utcoffset() is not None,
            "Timezone-aware candle time required"
        )

        expected = (
            schedule["open_utc"]
            + timedelta(minutes=5 * i)
        )

        require(
            value.astimezone(timezone.utc) == expected,
            "Candle timestamp disagrees with exchange schedule"
        )

    return schedule
