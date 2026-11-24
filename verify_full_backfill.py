"""JahvaScript full historical backfill audit.
Offline, read-only, research only.
"""

import hashlib
import json
from collections import defaultdict
from datetime import datetime, timedelta, time
from decimal import Decimal, InvalidOperation
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parent
HISTORY = ROOT / "journal/history"
ET = ZoneInfo("America/New_York")
INTERVAL = timedelta(minutes=5)

SEGMENTS = [
    ("batch_003", 400),
    ("batch_002", 400),
    ("backfill_001", 100),
    ("original", 500),
]


def require(ok, message):
    if not ok:
        raise ValueError("AUDIT FAILED: " + message)


def timestamp(bar):
    dt = datetime.fromisoformat(str(bar["time"]))
    require(
        dt.tzinfo is not None and dt.utcoffset() is not None,
        "Missing timezone"
    )
    return dt


def validate_bar(bar, symbol):
    dt = timestamp(bar)
    local = dt.astimezone(ET)

    require(
        bar.get("session") == "RTH",
        symbol + ": incorrect session"
    )

    require(
        local.weekday() < 5
        and time(9, 30) <= local.time() < time(16, 0)
        and local.minute % 5 == 0
        and local.second == 0
        and local.microsecond == 0,
        symbol + ": invalid five-minute timestamp"
    )

    try:
        o, h, l, c, v = [
            Decimal(str(bar[k]))
            for k in ("open", "high", "low", "close", "volume")
        ]
    except (
        KeyError, TypeError,
        ValueError, InvalidOperation
    ) as exc:
        raise ValueError(
            symbol + ": invalid OHLCV field"
        ) from exc

    require(
        all(x.is_finite() for x in (o, h, l, c, v))
        and min(o, h, l, c) > 0
        and l <= min(o, c)
        and h >= max(o, c)
        and v >= 0,
        symbol + ": invalid OHLCV values"
    )

    return dt


def segment_path(symbol, name):
    if name == "original":
        return HISTORY / f"{symbol}_M5.json"

    if name == "backfill_001":
        return (
            HISTORY / "backfill"
            / f"{symbol}_M5_backfill_001.json"
        )

    return (
        HISTORY / "backfill" / name
        / f"{symbol}_M5.json"
    )


def audit_symbol(symbol):
    print(f"\n--- {symbol} ---")

    segments = []

    for name, expected in SEGMENTS:
        path = segment_path(symbol, name)
        data = json.loads(path.read_text())

        require(
            data.get("symbol") == symbol
            and data.get("timeframe") == "M5"
            and data.get("execution_enabled") is False,
            symbol + ": invalid segment metadata"
        )

        if name != "original":
            require(
                data.get("trade_authorization") is False
                and data.get("research_only") is True,
                symbol + ": unsafe backfill metadata"
            )

        if name.startswith("batch_"):
            require(
                data.get("batch_number")
                == int(name.split("_")[1]),
                symbol + ": incorrect batch number"
            )

        bars = data["bars"]

        require(
            len(bars) == expected,
            f"{symbol} {name}: unexpected bar count"
        )

        times = [validate_bar(b, symbol) for b in bars]

        require(
            times == sorted(times)
            and len(times) == len(set(times)),
            symbol + ": segment ordering or duplicates"
        )

        segments.append({
            "name": name,
            "data": data,
            "bars": bars,
            "times": times
        })

        print(
            name,
            "| candles:", len(bars),
            "| oldest:", times[0].astimezone(ET).isoformat()
        )

    print("\nBATCH BOUNDARIES")

    for older, newer in zip(segments, segments[1:]):
        last_old = older["times"][-1]
        first_new = newer["times"][0]

        require(
            first_new - last_old == INTERVAL,
            symbol + ": five-minute batch seam gap"
        )

        require(
            older["data"].get("requested_end_ms")
            == int(first_new.timestamp() * 1000),
            symbol + ": request boundary metadata mismatch"
        )

        print(
            older["name"], "->", newer["name"],
            "| PASS"
        )

    all_times = [
        dt
        for segment in segments
        for dt in segment["times"]
    ]

    require(
        len(all_times) == 1400,
        symbol + ": wrong combined count"
    )
    require(
        len(all_times) == len(set(all_times)),
        symbol + ": combined duplicate timestamps"
    )
    require(
        all_times == sorted(all_times),
        symbol + ": combined ordering failure"
    )

    sessions = defaultdict(list)

    for dt in all_times:
        local = dt.astimezone(ET)
        sessions[local.date()].append(local)

    full = 0
    partial = 0
    internal_gaps = 0

    print("\nSESSION COVERAGE")

    for day, stamps in sorted(sessions.items()):
        gaps = sum(
            later - earlier != INTERVAL
            for earlier, later in zip(stamps, stamps[1:])
        )

        internal_gaps += gaps

        complete = (
            len(stamps) == 78
            and stamps[0].time() == time(9, 30)
            and stamps[-1].time() == time(15, 55)
            and gaps == 0
        )

        full += int(complete)
        partial += int(not complete)

        label = "FULL" if complete else "PARTIAL"

        print(
            str(day),
            "| candles:", len(stamps),
            "|", stamps[0].strftime("%H:%M"),
            "to", stamps[-1].strftime("%H:%M"),
            "|", label,
            "| gaps:", gaps
        )

    require(
        internal_gaps == 0,
        symbol + ": internal session gaps"
    )

    dates = sorted(sessions)
    absent_weekdays = []

    current = dates[0] + timedelta(days=1)

    while current < dates[-1]:
        if current.weekday() < 5 and current not in sessions:
            absent_weekdays.append(str(current))
        current += timedelta(days=1)

    print("\nTOTALS")
    print("Combined candles:", len(all_times))
    print("Trading dates observed:", len(sessions))
    print("Full standard sessions:", full)
    print("Partial sessions:", partial)
    print("Internal session gaps:", internal_gaps)
    print("Duplicate timestamps: 0")
    print(
        "Unobserved weekdays (calendar not verified):",
        ", ".join(absent_weekdays) or "none"
    )
    print("Combined timeline: PASS")

    return all_times


def main():
    print("\n=== JAHVASCRIPT FULL BACKFILL AUDIT ===")

    paths = [
        segment_path(symbol, name)
        for symbol in ("SPY", "QQQ")
        for name, _ in SEGMENTS
    ]

    reports = [
        ROOT / "journal/technical_indicators_latest.json",
        ROOT / "journal/historical_traps_latest.json",
        ROOT / "journal/historical_trap_context.json",
        ROOT / "journal/historical_trap_statistics.json",
        ROOT / "journal/trap_reliability_audit.json",
        ROOT / "journal/trap_cluster_audit.json",
    ]

    require(
        all(p.is_file() for p in paths),
        "One or more historical segments missing"
    )

    protected = paths + [
        p for p in reports if p.is_file()
    ]

    before = {
        str(p): hashlib.sha256(p.read_bytes()).hexdigest()
        for p in protected
    }

    spy_times = audit_symbol("SPY")
    qqq_times = audit_symbol("QQQ")

    require(
        spy_times == qqq_times,
        "SPY and QQQ timestamp alignment mismatch"
    )

    after = {
        str(p): hashlib.sha256(p.read_bytes()).hexdigest()
        for p in protected
    }

    require(
        before == after,
        "Historical or research file changed"
    )

    print("\n--- CROSS-SYMBOL VERIFICATION ---")
    print("SPY/QQQ aligned candles:", len(spy_times))
    print("Timestamp mismatches: 0")
    print("Protected file hashes: UNCHANGED")
    print("Original research reports: PRESERVED")

    print("\nAUDIT LIMITATIONS")
    print("Exchange calendar: NOT INDEPENDENTLY VERIFIED")
    print("Source prices: NOT INDEPENDENTLY VERIFIED")
    print("Newest candle finalized: NOT VERIFIED")
    print("Predictive performance: NOT ESTABLISHED")

    print("\nFULL BACKFILL AUDIT PASSED")
    print("HISTORICAL/RESEARCH FILES MODIFIED: NONE")
    print("WEBULL REQUESTS: NONE")
    print("AI REQUESTS: NONE")
    print("ORDER EXECUTION: DISABLED")


if __name__ == "__main__":
    main()
