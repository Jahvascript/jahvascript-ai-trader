"""JahvaScript historical data integrity audit.
Offline, read-only, research only.
"""

import json
from collections import Counter
from datetime import datetime, timedelta, time
from decimal import Decimal, InvalidOperation
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parent
HISTORY = ROOT / "journal/history"
EASTERN = ZoneInfo("America/New_York")

m5_times = {}

print("\n=== JAHVASCRIPT HISTORY INTEGRITY AUDIT ===")

for symbol in ("SPY", "QQQ"):
    for frame in ("M5", "D"):
        path = HISTORY / f"{symbol}_{frame}.json"

        print(f"\n--- {symbol} {frame} ---")

        if not path.is_file():
            print("ERROR: FILE MISSING")
            continue

        data = json.loads(path.read_text())
        bars = data.get("bars", [])

        timestamps = []
        bad_time = 0
        bad_ohlcv = 0
        bad_session = 0
        outside_hours = 0

        for bar in bars:
            try:
                dt = datetime.fromisoformat(bar["time"])
                if dt.tzinfo is None:
                    raise ValueError("Missing timezone")
                timestamps.append(dt)
            except (ValueError, TypeError, KeyError):
                bad_time += 1
                continue

            try:
                o, h, l, c, v = [
                    Decimal(str(bar[k]))
                    for k in (
                        "open", "high", "low",
                        "close", "volume"
                    )
                ]

                if (
                    not all(
                        x.is_finite()
                        for x in (o, h, l, c, v)
                    )
                    or min(o, h, l, c) <= 0
                    or v < 0
                    or h < max(o, c)
                    or l > min(o, c)
                ):
                    bad_ohlcv += 1
            except (
                KeyError, ValueError,
                TypeError, InvalidOperation
            ):
                bad_ohlcv += 1

            if frame == "M5":
                if bar.get("session") != "RTH":
                    bad_session += 1

                eastern = dt.astimezone(EASTERN)

                if not (
                    eastern.weekday() < 5
                    and time(9, 30)
                    <= eastern.time()
                    < time(16, 0)
                    and eastern.minute % 5 == 0
                    and eastern.second == 0
                ):
                    outside_hours += 1

        duplicates = len(timestamps) - len(set(timestamps))

        ascending = timestamps == sorted(timestamps)
        descending = timestamps == sorted(
            timestamps, reverse=True
        )

        print("Stored bars:", len(bars))
        print("Invalid timestamps:", bad_time)
        print("Duplicate timestamps:", duplicates)
        print("Invalid OHLCV:", bad_ohlcv)
        print("Ascending:", ascending)
        print("Descending:", descending)

        if timestamps:
            print("Earliest UTC:", min(timestamps).isoformat())
            print("Latest UTC:", max(timestamps).isoformat())

        if frame == "M5":
            print("Unexpected sessions:", bad_session)
            print("Outside standard RTH:", outside_hours)

            m5_times[symbol] = set(timestamps)

            sessions = {}
            for dt in timestamps:
                eastern = dt.astimezone(EASTERN)
                sessions.setdefault(
                    eastern.date(), set()
                ).add(eastern.time())

            print("\nPER-DAY COVERAGE")

            for day, observed in sorted(sessions.items()):
                first = min(observed)
                last = max(observed)

                # Check gaps only between first and last
                # observed bars, not uncollected day edges.
                current = datetime.combine(day, first)
                end = datetime.combine(day, last)

                expected = set()

                while current <= end:
                    expected.add(current.time())
                    current += timedelta(minutes=5)

                missing = sorted(expected - observed)

                print(
                    str(day),
                    "| bars:", len(observed),
                    "| first:", first.strftime("%H:%M"),
                    "| last:", last.strftime("%H:%M"),
                    "| internal gaps:", len(missing)
                )

                if missing:
                    print(
                        "  Missing examples:",
                        ", ".join(
                            x.strftime("%H:%M")
                            for x in missing[:5]
                        )
                    )

print("\n--- SPY / QQQ M5 ALIGNMENT ---")

if "SPY" in m5_times and "QQQ" in m5_times:
    spy = m5_times["SPY"]
    qqq = m5_times["QQQ"]

    print("Shared timestamps:", len(spy & qqq))
    print("SPY-only timestamps:", len(spy - qqq))
    print("QQQ-only timestamps:", len(qqq - spy))
else:
    print("Alignment: COULD NOT CHECK")

print("\nAUDIT LIMITATIONS")
print("Exchange holidays: NOT VALIDATED")
print("Early-close calendar: NOT VALIDATED")
print("Source price accuracy: NOT VERIFIED")
print("Live data freshness: NOT VERIFIED")

print("\nHISTORY INTEGRITY AUDIT COMPLETE")
print("FILES MODIFIED: NONE")
print("API REQUESTS: NONE")
print("ORDER EXECUTION: DISABLED")
