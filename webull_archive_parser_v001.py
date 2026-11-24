"""Strict parser for saved Webull research archive snapshots.

These archives are previously normalized JSON, NOT proven
to be original Webull HTTP response bodies.

No network requests, trading signals, or order execution.
"""

import json
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from zoneinfo import ZoneInfo

ET = ZoneInfo("America/New_York")
FIELDS = ("open", "high", "low", "close", "volume")


def require(ok, reason):
    if not ok:
        raise ValueError("WEBULL PARSER STOPPED: " + reason)


def parse_time(value):
    require(
        isinstance(value, str),
        "Candle timestamp must be a string"
    )
    dt = datetime.fromisoformat(value)
    require(
        dt.tzinfo is not None
        and dt.utcoffset() is not None,
        "Candle timestamp must include timezone"
    )
    return dt.astimezone(timezone.utc)


def parse_number(value, field):
    require(
        isinstance(value, (str, int, float))
        and not isinstance(value, bool),
        "Invalid " + field + " value type"
    )
    try:
        number = Decimal(str(value))
    except InvalidOperation as error:
        raise ValueError(
            "Invalid numeric " + field
        ) from error

    require(
        number.is_finite(),
        "Non-finite " + field
    )
    return number


def parse_bar(bar):
    require(
        isinstance(bar, dict)
        and all(key in bar for key in (*FIELDS, "time", "session")),
        "Missing candle fields"
    )

    dt = parse_time(bar["time"])
    local = dt.astimezone(ET)

    minute = local.hour * 60 + local.minute

    require(
        570 <= minute < 960
        and minute % 5 == 0
        and local.second == 0
        and local.microsecond == 0,
        "Candle timestamp is outside aligned RTH five-minute slots"
    )

    require(
        isinstance(bar["session"], str)
        and bool(bar["session"].strip()),
        "Missing session classification"
    )

    values = {
        field: parse_number(bar[field], field)
        for field in FIELDS
    }

    o, h, l, c, v = (
        values[field] for field in FIELDS
    )

    require(
        min(o, h, l, c) > 0
        and l <= min(o, c)
        and h >= max(o, c)
        and v >= 0,
        "Impossible OHLCV candle"
    )

    return {
        "time": dt.isoformat(),
        **values,
        "session": bar["session"]
    }


def parse_archive(raw_bytes, symbol, expected_count=None):
    require(
        symbol in ("SPY", "QQQ"),
        "Unsupported symbol"
    )
    require(
        isinstance(raw_bytes, bytes)
        and 0 < len(raw_bytes) <= 20_000_000,
        "Invalid archive byte payload"
    )

    payload = json.loads(raw_bytes)

    require(
        isinstance(payload, dict),
        "Archive root must be an object"
    )

    require(
        payload.get("symbol") == symbol,
        "Archive instrument mismatch"
    )

    require(
        str(payload.get("timeframe", "")).lower()
        in ("m5", "5m"),
        "Unexpected candle timeframe"
    )

    require(
        payload.get("research_only") is True
        and payload.get("execution_enabled") is False
        and payload.get("trade_authorization", False) is False,
        "Unsafe archive configuration"
    )

    bars = payload.get("bars")

    require(
        isinstance(bars, list)
        and len(bars) > 0,
        "Archive has no candle array"
    )

    if expected_count is not None:
        require(
            len(bars) == expected_count,
            "Unexpected archive candle count"
        )

    result = {}
    for bar in bars:
        parsed = parse_bar(bar)
        key = parsed["time"]

        require(
            key not in result,
            "Duplicate timestamp within archive"
        )

        result[key] = parsed

    return result


def merge_archives(parsed_batches):
    merged = {}
    provenance = {}

    for name, batch in parsed_batches:
        for key, candle in batch.items():
            require(
                key not in merged,
                "Duplicate candle across archived batches"
            )
            merged[key] = candle
            provenance[key] = name

    return merged, provenance
