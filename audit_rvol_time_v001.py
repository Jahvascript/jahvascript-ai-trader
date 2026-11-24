"""JahvaScript V001 time-of-day RVOL audit.
Offline exploratory statistics. No signals or orders.
"""

import hashlib
import json
import os
from collections import defaultdict
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parent
JOURNAL = ROOT / "journal"
DATASET = JOURNAL / "datasets/M5_SPY_QQQ_v001.json"
RESEARCH = JOURNAL / "expanded_research_v001.json"
ROBUSTNESS = JOURNAL / "rvol_robustness_v001.json"
OUTPUT = JOURNAL / "rvol_time_of_day_v001.json"

ET = ZoneInfo("America/New_York")
THRESHOLD = Decimal("1.2")
CONFIRMED = "CONFIRMED_REVERSAL"


def require(condition, message):
    if not condition:
        raise ValueError("AUDIT STOPPED: " + message)


def daypart(local):
    minutes = local.hour * 60 + local.minute

    if minutes < 11 * 60 + 30:
        return "MORNING"
    if minutes < 14 * 60:
        return "MIDDAY"
    return "AFTERNOON"


def summarize(events):
    high = [e for e in events if e["volume_group"] == "HIGH"]
    low = [e for e in events if e["volume_group"] == "LOW_NORMAL"]

    def result(rows):
        reversed_count = sum(
            e["outcome"] == CONFIRMED for e in rows
        )
        return {
            "reversed": reversed_count,
            "count": len(rows),
            "rate": (
                round(100 * reversed_count / len(rows), 1)
                if rows else None
            )
        }

    a = result(high)
    b = result(low)

    return {
        "high": a,
        "low_normal": b,
        "difference_points": (
            round(b["rate"] - a["rate"], 1)
            if a["rate"] is not None
            and b["rate"] is not None
            else None
        )
    }


def display(name, events):
    stats = summarize(events)
    a = stats["high"]
    b = stats["low_normal"]

    def fmt(row):
        if row["rate"] is None:
            return "N/A"
        return (
            f"{row['reversed']}/{row['count']} "
            f"({row['rate']:.1f}%)"
        )

    print(f"\n{name} | events={len(events)}")
    print("  HIGH:", fmt(a))
    print("  LOW_NORMAL:", fmt(b))
    print(
        "  LOW_NORMAL minus HIGH:",
        stats["difference_points"],
        "percentage points"
    )

    return stats


def main():
    print("\n=== JAHVASCRIPT RVOL TIME-OF-DAY AUDIT ===")

    require(
        "journal/" in (
            ROOT / ".gitignore"
        ).read_text().splitlines(),
        "Journal must be gitignored"
    )
    require(not OUTPUT.exists(), "Audit output already exists")

    raw = DATASET.read_bytes()
    dataset_hash = hashlib.sha256(raw).hexdigest()

    dataset = json.loads(raw)
    research = json.loads(RESEARCH.read_text())
    prior = json.loads(ROBUSTNESS.read_text())

    require(
        dataset.get("dataset_version") == "M5_SPY_QQQ_v001"
        and dataset.get("historical_research_only") is True
        and dataset.get("execution_enabled") is False
        and dataset.get("trade_authorization") is False
        and research.get("dataset_sha256") == dataset_hash
        and research.get("out_of_sample") is False
        and research.get("execution_enabled") is False
        and research.get("trade_authorization") is False
        and prior.get("dataset_sha256") == dataset_hash
        and prior.get("execution_enabled") is False
        and prior.get("trade_authorization") is False,
        "Dataset or research configuration mismatch"
    )

    full_days = set(dataset["full_standard_session_dates"])
    eligible = []
    excluded = 0
    original_count = 0

    for symbol in ("SPY", "QQQ"):
        sessions = defaultdict(list)

        for bar in dataset["symbols"][symbol]["bars"]:
            dt = datetime.fromisoformat(bar["time"])
            require(
                dt.tzinfo is not None
                and dt.utcoffset() is not None,
                "Unzoned historical candle"
            )
            day = dt.astimezone(ET).date().isoformat()
            if day in full_days:
                sessions[day].append(bar)

        index_by_timestamp = {}

        for day, bars in sessions.items():
            require(
                len(bars) == 78,
                symbol + ": incomplete session"
            )

            for index, bar in enumerate(bars):
                dt = datetime.fromisoformat(
                    bar["time"]
                ).astimezone(timezone.utc)

                key = dt.isoformat()
                require(
                    key not in index_by_timestamp,
                    "Duplicate candle timestamp"
                )
                index_by_timestamp[key] = (
                    day, index, bars
                )

        for event in research["symbols"][symbol]["events"]:
            original_count += 1

            require(
                event["symbol"] == symbol
                and event["trade_signal"] is False
                and event["direction"] in ("UP", "DOWN")
                and event["outcome"] in (
                    "CONFIRMED_REVERSAL",
                    "NO_FAILURE_WITHIN_HORIZON",
                    "PENDING"
                ),
                "Unexpected event metadata"
            )

            dt = datetime.fromisoformat(
                event["breakout_utc"]
            )

            require(
                dt.tzinfo is not None
                and dt.utcoffset() is not None,
                "Unzoned breakout"
            )

            key = dt.astimezone(timezone.utc).isoformat()
            require(key in index_by_timestamp, "Unknown event")

            day, index, bars = index_by_timestamp[key]

            require(
                index >= 12 and day == event["date"],
                "Breakout index/date mismatch"
            )

            # Require all three following five-minute
            # candles, even for early confirmed failures.
            if index + 3 >= len(bars):
                excluded += 1
                continue

            require(
                event["outcome"] != "PENDING",
                "Pending event with full horizon"
            )

            volumes = [
                Decimal(str(b["volume"]))
                for b in bars[index-5:index+1]
            ]

            require(
                all(
                    v.is_finite() and v >= 0
                    for v in volumes
                ),
                "Invalid volume"
            )

            baseline = sum(
                volumes[:5], Decimal(0)
            ) / Decimal(5)

            require(baseline > 0, "RVOL unavailable")

            exact_rvol = volumes[5] / baseline

            require(
                event["rvol"] is not None
                and abs(
                    exact_rvol
                    - Decimal(str(event["rvol"]))
                ) <= Decimal("0.000051"),
                "Historical RVOL mismatch"
            )

            local = dt.astimezone(ET)

            eligible.append({
                "symbol": symbol,
                "date": day,
                "cohort": event["cohort"],
                "direction": event["direction"],
                "daypart": daypart(local),
                "volume_group": (
                    "HIGH"
                    if exact_rvol >= THRESHOLD
                    else "LOW_NORMAL"
                ),
                "outcome": event["outcome"]
            })

    require(
        original_count == 328
        and excluded == 23
        and len(eligible) == 305,
        "Eligibility totals disagree with prior audit"
    )

    overall = summarize(eligible)

    require(
        overall["high"]["count"] == 164
        and overall["high"]["reversed"] == 63
        and overall["low_normal"]["count"] == 141
        and overall["low_normal"]["reversed"] == 87,
        "Previous RVOL results not reproduced"
    )

    require(
        prior["eligible_events"] == len(eligible)
        and prior["excluded_events"] == excluded,
        "Robustness report reconciliation failure"
    )

    print("Original attempts:", original_count)
    print("Full-horizon eligible:", len(eligible))
    print("Excluded:", excluded)

    results = {
        "overall": display("OVERALL", eligible),
        "by_daypart": {},
        "by_direction_daypart": {},
        "by_cohort_daypart": {}
    }

    for part in ("MORNING", "MIDDAY", "AFTERNOON"):
        rows = [e for e in eligible if e["daypart"] == part]
        results["by_daypart"][part] = display(
            "DAYPART " + part, rows
        )

    for direction in ("UP", "DOWN"):
        for part in ("MORNING", "MIDDAY", "AFTERNOON"):
            rows = [
                e for e in eligible
                if e["direction"] == direction
                and e["daypart"] == part
            ]
            label = direction + "_" + part
            results["by_direction_daypart"][label] = display(
                label, rows
            )

    for cohort in (
        "EARLIER_BACKFILL_EXPLORATORY",
        "PREVIOUSLY_EXAMINED_PERIOD"
    ):
        for part in ("MORNING", "MIDDAY", "AFTERNOON"):
            rows = [
                e for e in eligible
                if e["cohort"] == cohort
                and e["daypart"] == part
            ]
            label = cohort + "_" + part
            results["by_cohort_daypart"][label] = display(
                label, rows
            )

    # Descriptive common-weight comparison:
    # only direction/daypart groups with at least
    # five events in EACH volume category.
    weighted_sum = 0.0
    included = 0
    accepted = []
    excluded_strata = []

    for name, stats in results[
        "by_direction_daypart"
    ].items():
        high = stats["high"]["count"]
        low = stats["low_normal"]["count"]

        if high < 5 or low < 5:
            excluded_strata.append(name)
            continue

        weight = high + low
        weighted_sum += (
            stats["difference_points"] * weight
        )
        included += weight
        accepted.append(name)

    standardized = (
        round(weighted_sum / included, 1)
        if included else None
    )

    print("\nDIRECTION + DAYPART COMMON-WEIGHT CHECK")
    print("Eligible strata retained:", len(accepted))
    print("Events in retained strata:", included)
    print(
        "Exploratory weighted LOW_NORMAL minus HIGH:",
        standardized,
        "percentage points"
    )
    print(
        "Sparse strata omitted:",
        ", ".join(excluded_strata) or "none"
    )

    results["stratified_comparison"] = {
        "retained_strata": accepted,
        "omitted_strata": excluded_strata,
        "events_covered": included,
        "weighted_difference_points": standardized,
        "minimum_each_group_per_stratum": 5
    }

    report = {
        "generated_utc": datetime.now(
            timezone.utc
        ).isoformat(),
        "dataset_sha256": dataset_hash,
        "original_events": original_count,
        "eligible_events": len(eligible),
        "excluded_events": excluded,
        "rvol_threshold": str(THRESHOLD),
        "dayparts_eastern": {
            "MORNING": "before 11:30",
            "MIDDAY": "11:30 to before 14:00",
            "AFTERNOON": "14:00 onward"
        },
        "results": results,
        "stratification_exploratory": True,
        "time_of_day_confounding_eliminated": False,
        "out_of_sample": False,
        "predictive_validity_established": False,
        "historical_research_only": True,
        "trade_signal": False,
        "execution_enabled": False,
        "trade_authorization": False
    }

    os.umask(0o077)

    with OUTPUT.open("x") as handle:
        json.dump(report, handle, indent=2)
        handle.write("\n")

    OUTPUT.chmod(0o600)

    print("\nLIMITATIONS")
    print("RVOL uses preceding 5 bars, not clock-time norms")
    print("Small strata may have unstable percentages")
    print("Market regimes and correlated events remain")
    print("No statistical significance or causal claim")
    print("Out-of-sample predictive test: NONE")
    print("Source prices independently verified: NO")
    print("Existing historical datasets: UNCHANGED")
    print("Saved: rvol_time_of_day_v001.json")

    print("\nRVOL TIME-OF-DAY AUDIT PASSED")
    print("WEBULL REQUESTS: NONE")
    print("AI REQUESTS: NONE")
    print("ORDER EXECUTION: DISABLED")


if __name__ == "__main__":
    main()
