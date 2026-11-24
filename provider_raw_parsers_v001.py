"""Strict parsing of saved provider responses.

Yahoo chart/5m supported.
Webull raw format must be inspected before implementation.
No network requests or trading operations.
"""

import json
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from zoneinfo import ZoneInfo

ET = ZoneInfo("America/New_York")
FIELDS = ("open", "high", "low", "close", "volume")
STEP = timedelta(minutes=5)


def require(condition, reason):
    if not condition:
        raise ValueError("PROVIDER PARSER STOPPED: " + reason)


def parse_yahoo(raw_bytes, symbol, session_dates):
    """Reconstruct 78 RTH bars per selected session.

    Input: original Yahoo chart JSON bytes.
    Output: {YYYY-MM-DD: [normalized candle dictionaries]}.

    This does not certify exchange accuracy.
    """
    require(
        symbol in ("SPY", "QQQ"),
        "Unsupported instrument"
    )
    require(
        isinstance(raw_bytes, bytes)
        and 0 < len(raw_bytes) <= 16_000_000,
        "Invalid raw response size/type"
    )

    dates = set(session_dates)
    require(dates, "No session dates supplied")

    payload = json.loads(raw_bytes)
    chart = payload["chart"]

    require(
        chart.get("error") is None,
        "Provider reported an error"
    )

    results = chart["result"]

    require(
        isinstance(results, list)
        and len(results) == 1,
        "Unexpected result count"
    )

    result = results[0]
    metadata = result["meta"]

    require(
        metadata.get("symbol") == symbol
        and metadata.get("exchangeTimezoneName")
            == "America/New_York",
        "Instrument or timezone mismatch"
    )

    timestamps = result["timestamp"]
    quote_sets = result["indicators"]["quote"]

    require(
        isinstance(timestamps, list)
        and isinstance(quote_sets, list)
        and len(quote_sets) == 1,
        "Invalid Yahoo quote structure"
    )

    quote = quote_sets[0]

    require(
        all(
            isinstance(quote.get(field), list)
            and len(quote[field]) == len(timestamps)
            for field in FIELDS
        ),
        "Quote field lengths differ"
    )

    sessions = {day: [] for day in dates}

    for i, stamp in enumerate(timestamps):
        require(
            isinstance(stamp, int)
            and not isinstance(stamp, bool),
            "Invalid Unix timestamp"
        )

        dt = datetime.fromtimestamp(
            stamp, timezone.utc
        )
        local = dt.astimezone(ET)
        day = local.date().isoformat()

        if day not in dates:
            continue

        minute = local.hour * 60 + local.minute

        if not 570 <= minute < 960:
            continue

        numbers = {}

        for field in FIELDS:
            value = quote[field][i]

            require(
                value is not None
                and not isinstance(value, bool),
                "Missing or invalid " + field
            )

            number = Decimal(str(value))

            require(
                number.is_finite(),
                "Non-finite " + field
            )

            numbers[field] = number

        o = numbers["open"]
        h = numbers["high"]
        l = numbers["low"]
        c = numbers["close"]
        v = numbers["volume"]

        require(
            min(o, h, l, c) > 0
            and h >= max(o, c)
            and l <= min(o, c)
            and v >= 0,
            "Impossible OHLCV candle"
        )

        sessions[day].append({
            "time": dt.isoformat(),
            **numbers
        })

    for day in sorted(dates):
        bars = sessions[day]

        require(
            len(bars) == 78,
            symbol + " " + day
            + ": expected 78 RTH bars"
        )

        times = [
            datetime.fromisoformat(bar["time"])
            for bar in bars
        ]

        expected_start = datetime.fromisoformat(
            day + "T09:30:00"
        ).replace(tzinfo=ET).astimezone(
            timezone.utc
        )

        require(
            len(set(times)) == 78
            and all(
                time == expected_start + i * STEP
                for i, time in enumerate(times)
            ),
            symbol + " " + day
            + ": incorrect five-minute timestamp sequence"
        )

    return dict(sorted(sessions.items()))
