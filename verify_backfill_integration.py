"""JahvaScript historical backfill integration audit.
Offline, read-only research verification.
"""

import hashlib
import json
from collections import defaultdict
from datetime import datetime, timedelta, time
from decimal import Decimal
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parent
HISTORY = ROOT / "journal/history"
BACKFILL = HISTORY / "backfill"
ET = ZoneInfo("America/New_York")
FIVE_MINUTES = timedelta(minutes=5)


def require(condition, message):
    if not condition:
        raise ValueError("AUDIT FAILED: " + message)


def timestamp(bar):
    dt = datetime.fromisoformat(str(bar["time"]))
    require(
        dt.tzinfo is not None and dt.utcoffset() is not None,
        "Missing timezone"
    )
    return dt


def verify_bar(bar, symbol):
    dt = timestamp(bar)
    local = dt.astimezone(ET)

    require(
        bar.get("session") == "RTH",
        symbol + ": unexpected session"
    )
    require(
        local.weekday() < 5
        and time(9, 30) <= local.time() < time(16, 0)
        and local.minute % 5 == 0
        and local.second == 0
        and local.microsecond == 0,
        symbol + ": invalid five-minute timestamp"
    )

    o, h, l, c, v = (
        Decimal(str(bar[key]))
        for key in ("open", "high", "low", "close", "volume")
    )

    require(
        all(x.is_finite() for x in (o, h, l, c, v))
        and min(o, h, l, c) > 0
        and l <= min(o, c)
        and h >= max(o, c)
        and v >= 0,
        symbol + ": invalid OHLCV"
    )

    return dt


def load_and_audit(symbol):
    original_path = HISTORY / f"{symbol}_M5.json"
    backfill_path = (
        BACKFILL / f"{symbol}_M5_backfill_001.json"
    )

    original = json.loads(original_path.read_text())
    backfill = json.loads(backfill_path.read_text())

    require(
        original.get("symbol") == symbol
        and original.get("timeframe") == "M5"
        and original.get("execution_enabled") is False,
        symbol + ": invalid original metadata"
    )

    require(
        backfill.get("symbol") == symbol
        and backfill.get("timeframe") == "M5"
        and backfill.get("research_only") is True
        and backfill.get("execution_enabled") is False
        and backfill.get("trade_authorization") is False,
        symbol + ": invalid backfill metadata"
    )

    old_bars = original["bars"]
    new_bars = backfill["bars"]

    require(
        len(old_bars) == 500 and len(new_bars) == 100,
        symbol + ": unexpected bar counts"
    )

    old_times = [
        verify_bar(b, symbol) for b in old_bars
    ]
    new_times = [
        verify_bar(b, symbol) for b in new_bars
    ]

    require(
        old_times == sorted(old_times)
        and new_times == sorted(new_times),
        symbol + ": unordered candles"
    )

    require(
        new_times[-1] + FIVE_MINUTES == old_times[0],
        symbol + ": backfill seam does not connect"
    )

    require(
        backfill.get("requested_end_ms")
        == int(old_times[0].timestamp() * 1000),
        symbol + ": requested boundary mismatch"
    )

    combined = new_bars + old_bars
    times = new_times + old_times

    require(
        len(times) == len(set(times)),
        symbol + ": duplicate timestamps"
    )
    require(
        times == sorted(times),
        symbol + ": combined order incorrect"
    )

    sessions = defaultdict(list)

    for dt in times:
        local = dt.astimezone(ET)
        sessions[local.date().isoformat()].append(local)

    gap_count = 0
    full_sessions = 0

    print(f"\n--- {symbol} ---")
    print("Original candles:", len(old_bars))
    print("Backfilled candles:", len(new_bars))
    print("Combined candles:", len(combined))
    print("Duplicates: 0")
    print("Boundary continuity: PASS")
    print("Earliest:", times[0].astimezone(ET).isoformat())
    print("Latest:", times[-1].astimezone(ET).isoformat())

    print("\nSESSION COVERAGE")

    for day, stamps in sorted(sessions.items()):
        missing = []

        for earlier, later in zip(stamps, stamps[1:]):
            if later - earlier != FIVE_MINUTES:
                missing.append(
                    (earlier.isoformat(), later.isoformat())
                )

        gap_count += len(missing)

        complete = (
            len(stamps) == 78
            and stamps[0].time() == time(9, 30)
            and stamps[-1].time() == time(15, 55)
            and not missing
        )

        full_sessions += int(complete)

        label = "FULL" if complete else "PARTIAL"

        print(
            f"{day} | {len(stamps)} bars | "
            f"{stamps[0]:%H:%M}–"
            f"{stamps[-1]:%H:%M} | {label}"
        )

        for start, end in missing[:3]:
            print("  GAP:", start, "->", end)

    require(gap_count == 0, symbol + ": internal gaps found")

    print("\nComplete standard sessions:", full_sessions)
    print("Internal session gaps:", gap_count)
    print("Combined timeline integrity: PASS")

    return times


def main():
    print("\n=== JAHVASCRIPT BACKFILL INTEGRATION AUDIT ===")

    paths = [
        HISTORY / f"{symbol}_M5.json"
        for symbol in ("SPY", "QQQ")
    ] + [
        BACKFILL / f"{symbol}_M5_backfill_001.json"
        for symbol in ("SPY", "QQQ")
    ]

    require(
        all(p.is_file() for p in paths),
        "One or more history files missing"
    )

    before = {
        str(p): hashlib.sha256(p.read_bytes()).hexdigest()
        for p in paths
    }

    spy = load_and_audit("SPY")
    qqq = load_and_audit("QQQ")

    require(
        spy == qqq,
        "SPY/QQQ combined timestamps differ"
    )

    after = {
        str(p): hashlib.sha256(p.read_bytes()).hexdigest()
        for p in paths
    }

    require(before == after, "Source file changed")

    print("\nCROSS-SYMBOL CHECK")
    print("SPY/QQQ aligned timestamps:", len(spy))
    print("Timestamp mismatches: 0")
    print("Original/backfill file hashes unchanged: PASS")

    print("\nLIMITATIONS")
    print("Exchange holiday calendar: NOT VERIFIED")
    print("Source prices: NOT INDEPENDENTLY VERIFIED")
    print("Latest candle finalization: NOT VERIFIED")
    print("Trading strategy validity: NOT ESTABLISHED")

    print("\nBACKFILL INTEGRATION AUDIT PASSED")
    print("HISTORICAL DATA FILES MODIFIED: NONE")
    print("WEBULL REQUESTS: NONE")
    print("AI REQUESTS: NONE")
    print("ORDER EXECUTION: DISABLED")


if __name__ == "__main__":
    main()
