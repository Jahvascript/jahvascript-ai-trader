"""Independently verify reusable RVOL features on V001.
Read-only, offline verification.
"""

import hashlib
import json
from collections import Counter, defaultdict
from datetime import datetime, timezone, timedelta
from decimal import Decimal
from pathlib import Path
from zoneinfo import ZoneInfo

from rvol_features import calculate_dual_rvol

ROOT = Path(__file__).resolve().parent
JOURNAL = ROOT / "journal"
ET = ZoneInfo("America/New_York")

DATASET = JOURNAL / "datasets/M5_SPY_QQQ_v001.json"
RESEARCH = JOURNAL / "rvol_clock_normalized_v001.json"

TOLERANCE = Decimal("0.000051")


def require(condition, message):
    if not condition:
        raise ValueError("FEATURE AUDIT FAILED: " + message)


def test_synthetic():
    result = calculate_dual_rvol(
        120, [100] * 5, [200] * 5
    )

    require(
        result["rolling_rvol"] == Decimal("1.2"),
        "Rolling RVOL calculation"
    )

    require(
        result["clock_rvol"] == Decimal("0.6"),
        "Clock RVOL calculation"
    )

    require(
        result["agreement_state"] == "HIGH_TO_LOW_NORMAL",
        "Agreement state"
    )

    require(result["trade_signal"] is False, "Unsafe output")

    try:
        calculate_dual_rvol(120, [100] * 4, [200] * 5)
    except ValueError:
        pass
    else:
        raise ValueError("Invalid baseline was accepted")

    print("Synthetic feature tests: PASS")
    print("Invalid baseline rejection: PASS")


def main():
    print("\n=== JAHVASCRIPT DUAL-RVOL FEATURE AUDIT ===")

    require(
        "journal/" in (
            ROOT / ".gitignore"
        ).read_text().splitlines(),
        "Journal must be gitignored"
    )

    test_synthetic()

    dataset_bytes = DATASET.read_bytes()
    research_bytes = RESEARCH.read_bytes()

    dataset = json.loads(dataset_bytes)
    research = json.loads(research_bytes)

    fingerprint = hashlib.sha256(
        dataset_bytes
    ).hexdigest()

    require(
        dataset.get("dataset_version") == "M5_SPY_QQQ_v001"
        and dataset.get("historical_research_only") is True
        and dataset.get("execution_enabled") is False
        and dataset.get("trade_authorization") is False,
        "Unsafe dataset"
    )

    require(
        research.get("dataset_sha256") == fingerprint
        and research.get("historical_research_only") is True
        and research.get("execution_enabled") is False
        and research.get("trade_authorization") is False,
        "Unsafe research report"
    )

    dates = sorted(dataset["full_standard_session_dates"])
    require(len(dates) == 17, "Unexpected session coverage")

    lookups = {}

    for symbol in ("SPY", "QQQ"):
        by_day = defaultdict(list)

        for bar in dataset["symbols"][symbol]["bars"]:
            dt = datetime.fromisoformat(bar["time"])

            require(
                dt.tzinfo is not None
                and dt.utcoffset() is not None,
                "Missing candle timezone"
            )

            local = dt.astimezone(ET)
            day = local.date().isoformat()

            if day in dates:
                by_day[day].append({
                    "time": dt.astimezone(timezone.utc),
                    "slot": local.strftime("%H:%M"),
                    "volume": Decimal(str(bar["volume"]))
                })

        for day in dates:
            candles = by_day[day]

            require(
                len(candles) == 78
                and candles[0]["slot"] == "09:30"
                and candles[-1]["slot"] == "15:55",
                "Incomplete reference session"
            )

            require(
                all(
                    b["time"] - a["time"]
                    == timedelta(minutes=5)
                    for a, b in zip(candles, candles[1:])
                ),
                "Reference session gap"
            )

        lookups[symbol] = by_day

    transitions = Counter()
    verified = 0
    max_difference = Decimal(0)
    seen = set()

    for item in research["events"]:
        symbol = item["symbol"]
        require(symbol in lookups, "Unknown symbol")

        dt = datetime.fromisoformat(
            item["timestamp"]
        ).astimezone(timezone.utc)

        day = item["day"]
        require(day in dates, "Unknown event date")

        candles = lookups[symbol][day]

        matches = [
            i for i, candle in enumerate(candles)
            if candle["time"] == dt
        ]

        require(len(matches) == 1, "Event candle not found")

        index = matches[0]

        require(
            12 <= index
            and index + 3 < len(candles),
            "Invalid event research window"
        )

        key = (symbol, dt)
        require(key not in seen, "Duplicate event")
        seen.add(key)

        day_index = dates.index(day)

        require(
            day_index >= 5,
            "Insufficient historical sessions"
        )

        previous_dates = dates[day_index-5:day_index]

        require(
            item["reference_days"] == previous_dates
            and all(d < day for d in previous_dates),
            "Future session entered clock-time baseline"
        )

        # Restrict the current session to observations
        # available through this event candle.
        known_candles = candles[:index+1]

        preceding = [
            candle["volume"]
            for candle in known_candles[-6:-1]
        ]

        slot = known_candles[-1]["slot"]

        clock_references = []

        for previous_day in previous_dates:
            matching = [
                bar["volume"]
                for bar in lookups[symbol][previous_day]
                if bar["slot"] == slot
            ]

            require(
                len(matching) == 1,
                "Missing clock-time reference"
            )

            clock_references.append(matching[0])

        result = calculate_dual_rvol(
            known_candles[-1]["volume"],
            preceding,
            clock_references
        )

        for computed, stored in (
            ("rolling_rvol", "rolling_rvol"),
            ("clock_rvol", "clock_rvol")
        ):
            difference = abs(
                result[computed]
                - Decimal(str(item[stored]))
            )

            require(
                difference <= TOLERANCE,
                "Historical feature value mismatch"
            )

            max_difference = max(
                max_difference, difference
            )

        require(
            result["rolling_group"] == item["rolling_group"]
            and result["clock_group"] == item["clock_group"],
            "RVOL category mismatch"
        )

        require(
            result["trade_signal"] is False,
            "Feature produced a trade signal"
        )

        transitions[result["agreement_state"]] += 1
        verified += 1

    require(
        verified == 239
        and research["eligible_events"] == verified,
        "Verification count mismatch"
    )

    changed = (
        transitions["HIGH_TO_LOW_NORMAL"]
        + transitions["LOW_NORMAL_TO_HIGH"]
    )

    require(
        changed == research["classification_changes"] == 87,
        "Disagreement reconciliation failure"
    )

    require(
        hashlib.sha256(DATASET.read_bytes()).hexdigest()
        == fingerprint,
        "Dataset changed during verification"
    )

    require(
        hashlib.sha256(RESEARCH.read_bytes()).hexdigest()
        == hashlib.sha256(research_bytes).hexdigest(),
        "Research report changed"
    )

    print("\nHistorical events verified:", verified)
    print("Maximum RVOL difference:", max_difference)

    print("\nAGREEMENT STATES")
    for name in (
        "HIGH_TO_HIGH",
        "HIGH_TO_LOW_NORMAL",
        "LOW_NORMAL_TO_HIGH",
        "LOW_NORMAL_TO_LOW_NORMAL"
    ):
        print(name + ":", transitions[name])

    print("\nHistorical feature reproduction: PASS")
    print("Past-only reference dates: PASS")
    print("Original dataset/report hashes: UNCHANGED")
    print("Trade signals generated: NONE")
    print("Predictive validity established: NO")
    print("\nDUAL-RVOL FEATURE ENGINE PASSED")
    print("WEBULL REQUESTS: NONE")
    print("AI REQUESTS: NONE")
    print("ORDER EXECUTION: DISABLED")


if __name__ == "__main__":
    main()
