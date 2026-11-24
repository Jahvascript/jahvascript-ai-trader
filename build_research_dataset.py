"""JahvaScript versioned historical research snapshot.
Offline only. Never modifies original market data.
"""

import hashlib
import json
import os
from collections import defaultdict
from datetime import datetime, timezone, time, timedelta
from decimal import Decimal
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parent
HISTORY = ROOT / "journal/history"
DEST = ROOT / "journal/datasets/M5_SPY_QQQ_v001.json"
ET = ZoneInfo("America/New_York")
STEP = timedelta(minutes=5)

SEGMENTS = (
    ("backfill_003", 400),
    ("backfill_002", 400),
    ("backfill_001", 100),
    ("original", 500),
)


def require(ok, message):
    if not ok:
        raise ValueError("DATASET BUILD STOPPED: " + message)


def source_path(symbol, segment):
    if segment == "original":
        return HISTORY / f"{symbol}_M5.json"
    if segment == "backfill_001":
        return (
            HISTORY / "backfill"
            / f"{symbol}_M5_backfill_001.json"
        )
    return (
        HISTORY / "backfill"
        / segment.replace("backfill_", "batch_")
        / f"{symbol}_M5.json"
    )


def parse_time(value):
    dt = datetime.fromisoformat(str(value))
    require(
        dt.tzinfo is not None
        and dt.utcoffset() is not None,
        "Timestamp lacks timezone"
    )
    return dt.astimezone(timezone.utc)


def validate_bar(bar):
    dt = parse_time(bar["time"])
    local = dt.astimezone(ET)

    require(
        bar.get("session") == "RTH"
        and local.weekday() < 5
        and time(9, 30) <= local.time() < time(16, 0)
        and local.minute % 5 == 0
        and local.second == 0
        and local.microsecond == 0,
        "Invalid regular-hours candle timestamp"
    )

    values = [
        Decimal(str(bar[key]))
        for key in ("open", "high", "low", "close", "volume")
    ]

    o, h, l, c, v = values

    require(
        all(value.is_finite() for value in values)
        and min(o, h, l, c) > 0
        and l <= min(o, c)
        and h >= max(o, c)
        and v >= 0,
        "Invalid OHLCV"
    )

    return dt


def gather(symbol):
    combined = []
    all_times = []
    sources = []

    for segment, expected in SEGMENTS:
        path = source_path(symbol, segment)
        require(path.is_file(), f"Missing {path}")

        raw_bytes = path.read_bytes()
        data = json.loads(raw_bytes)

        require(
            data.get("symbol") == symbol
            and data.get("timeframe") == "M5"
            and data.get("execution_enabled") is False,
            f"{symbol} {segment}: invalid metadata"
        )

        if segment != "original":
            require(
                data.get("research_only") is True
                and data.get("trade_authorization") is False,
                f"{symbol} {segment}: unsafe metadata"
            )

        bars = data["bars"]
        require(
            len(bars) == expected,
            f"{symbol} {segment}: unexpected count"
        )

        times = [validate_bar(bar) for bar in bars]

        require(
            times == sorted(times)
            and len(times) == len(set(times)),
            f"{symbol} {segment}: order/duplicate failure"
        )

        if all_times:
            require(
                times[0] - all_times[-1] == STEP,
                f"{symbol}: segment boundary gap"
            )

        all_times.extend(times)
        combined.extend(bars)

        sources.append({
            "segment": segment,
            "file": str(path.relative_to(ROOT)),
            "sha256": hashlib.sha256(raw_bytes).hexdigest(),
            "candles": len(bars)
        })

    require(
        len(combined) == 1400
        and len(all_times) == len(set(all_times))
        and all_times == sorted(all_times),
        f"{symbol}: combined timeline invalid"
    )

    by_day = defaultdict(list)

    for dt in all_times:
        local = dt.astimezone(ET)
        by_day[local.date().isoformat()].append(local)

    sessions = []

    for day, stamps in sorted(by_day.items()):
        require(
            all(
                b - a == STEP
                for a, b in zip(stamps, stamps[1:])
            ),
            f"{symbol} {day}: internal candle gap"
        )

        complete = (
            len(stamps) == 78
            and stamps[0].time() == time(9, 30)
            and stamps[-1].time() == time(15, 55)
        )

        sessions.append({
            "date": day,
            "candles": len(stamps),
            "first_eastern": stamps[0].strftime("%H:%M"),
            "last_eastern": stamps[-1].strftime("%H:%M"),
            "standard_session_complete": complete
        })

    return combined, all_times, sources, sessions


def main():
    print("\n=== JAHVASCRIPT RESEARCH DATASET V001 ===")

    require(
        "journal/" in (
            ROOT / ".gitignore"
        ).read_text().splitlines(),
        "Journal must be gitignored"
    )

    symbols = {}
    timelines = {}
    all_sources = []
    coverage = {}

    for symbol in ("SPY", "QQQ"):
        bars, times, sources, sessions = gather(symbol)

        symbols[symbol] = {"bars": bars}
        timelines[symbol] = times
        all_sources.extend(sources)
        coverage[symbol] = sessions

        print(f"\n--- {symbol} ---")
        print("Combined candles:", len(bars))
        print("Source segments:", len(sources))
        print(
            "Complete standard sessions:",
            sum(s["standard_session_complete"] for s in sessions)
        )
        print(
            "Partial sessions:",
            sum(not s["standard_session_complete"] for s in sessions)
        )
        print("Earliest:", times[0].isoformat())
        print("Latest:", times[-1].isoformat())

    require(
        timelines["SPY"] == timelines["QQQ"],
        "SPY/QQQ timestamps differ"
    )
    require(
        coverage["SPY"] == coverage["QQQ"],
        "SPY/QQQ session coverage differs"
    )

    sessions = coverage["SPY"]
    full_days = [
        s["date"] for s in sessions
        if s["standard_session_complete"]
    ]
    partial_days = [
        s["date"] for s in sessions
        if not s["standard_session_complete"]
    ]

    require(
        len(full_days) == 17
        and len(partial_days) == 2,
        "Unexpected session coverage"
    )

    payload = {
        "dataset_version": "M5_SPY_QQQ_v001",
        "created_utc": datetime.now(
            timezone.utc
        ).isoformat(),
        "source": "WEBULL_SANDBOX_HISTORICAL",
        "timeframe": "M5",
        "symbols": symbols,
        "source_manifest": all_sources,
        "session_coverage": sessions,
        "full_standard_session_dates": full_days,
        "partial_session_dates": partial_days,
        "independent_price_verification": False,
        "exchange_calendar_verified": False,
        "latest_bar_finalization_verified": False,
        "untouched_out_of_sample_test": False,
        "historical_research_only": True,
        "trade_signal": False,
        "execution_enabled": False,
        "trade_authorization": False
    }

    DEST.parent.mkdir(parents=True, exist_ok=True)
    os.chmod(DEST.parent, 0o700)

    if DEST.exists():
        existing = json.loads(DEST.read_text())

        require(
            existing.get("dataset_version")
            == payload["dataset_version"]
            and existing.get("source_manifest")
            == payload["source_manifest"]
            and existing.get("symbols") == symbols
            and existing.get("session_coverage") == sessions
            and existing.get("execution_enabled") is False
            and existing.get("trade_authorization") is False,
            "Existing V001 does not match sources; not overwriting"
        )
        print("\nExisting V001 snapshot: VERIFIED, NOT OVERWRITTEN")
    else:
        os.umask(0o077)
        with DEST.open("x") as handle:
            json.dump(payload, handle, indent=2)
            handle.write("\n")
        DEST.chmod(0o600)
        print("\nNew immutable-by-workflow V001 snapshot: CREATED")

    digest = hashlib.sha256(DEST.read_bytes()).hexdigest()

    for entry in all_sources:
        path = ROOT / entry["file"]
        require(
            hashlib.sha256(path.read_bytes()).hexdigest()
            == entry["sha256"],
            "Raw source changed during build"
        )

    print("\n--- DATASET MANIFEST ---")
    print("Dataset:", DEST.relative_to(ROOT))
    print("SHA-256:", digest)
    print("Source files fingerprinted:", len(all_sources))
    print("SPY/QQQ alignment: PASS")
    print("Full standard session dates:", len(full_days))
    print("Partial session dates:", ", ".join(partial_days))
    print("Original source hashes: VERIFIED")
    print("Existing raw datasets: UNCHANGED")
    print("Earlier research reports: UNCHANGED")
    print("Out-of-sample status: NOT UNTOUCHED")
    print("Source price accuracy: NOT INDEPENDENTLY VERIFIED")

    print("\nRESEARCH DATASET V001 PASSED")
    print("WEBULL REQUESTS: NONE")
    print("AI REQUESTS: NONE")
    print("ORDER EXECUTION: DISABLED")


if __name__ == "__main__":
    main()
