"""JahvaScript clock-time RVOL research.
Offline, historical, outcome-blind volume features.
No APIs, AI requests or trading.
"""

import hashlib
import json
import os
from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parent
JOURNAL = ROOT / "journal"
DATASET = JOURNAL / "datasets/M5_SPY_QQQ_v001.json"
RESEARCH = JOURNAL / "expanded_research_v001.json"
OUTPUT = JOURNAL / "rvol_clock_normalized_v001.json"

ET = ZoneInfo("America/New_York")
THRESHOLD = Decimal("1.2")
BASELINE_DAYS = 5
HORIZON = 3
SPACING = timedelta(minutes=15)

CONFIRMED = "CONFIRMED_REVERSAL"
SURVIVED = "NO_FAILURE_WITHIN_HORIZON"
PENDING = "PENDING"


def require(ok, message):
    if not ok:
        raise ValueError("AUDIT STOPPED: " + message)


def group(value):
    return "HIGH" if value >= THRESHOLD else "LOW_NORMAL"


def summary(rows, field):
    result = {}

    for category in ("HIGH", "LOW_NORMAL"):
        subset = [r for r in rows if r[field] == category]
        failures = sum(
            r["outcome"] == CONFIRMED for r in subset
        )

        result[category] = {
            "reversals": failures,
            "total": len(subset),
            "percentage": (
                round(100 * failures / len(subset), 1)
                if subset else None
            )
        }

    high = result["HIGH"]["percentage"]
    lower = result["LOW_NORMAL"]["percentage"]

    result["difference_points"] = (
        round(lower - high, 1)
        if high is not None and lower is not None
        else None
    )

    return result


def display(name, rows):
    print("\n" + name + " | events:", len(rows))

    results = {}

    for field, label in (
        ("rolling_group", "PREVIOUS-5-BAR RVOL"),
        ("clock_group", "SAME-CLOCK-TIME RVOL")
    ):
        stats = summary(rows, field)
        results[field] = stats

        print(" ", label)

        for category in ("HIGH", "LOW_NORMAL"):
            value = stats[category]
            percentage = value["percentage"]

            text = (
                f"{percentage:.1f}%"
                if percentage is not None else "N/A"
            )

            print(
                f"   {category}: "
                f"{value['reversals']}/"
                f"{value['total']} ({text})"
            )

        print(
            "   LOW_NORMAL minus HIGH:",
            stats["difference_points"],
            "percentage points"
        )

    return results


def cluster(rows, preferred):
    by_date = defaultdict(list)

    for event in rows:
        by_date[event["day"]].append(event)

    selected = []

    for day, events in sorted(by_date.items()):
        ordered = sorted(
            events,
            key=lambda e: (
                e["timestamp"],
                e["symbol"] != preferred
            )
        )

        last_time = None

        for event in ordered:
            if (
                last_time is not None
                and event["timestamp"] - last_time <= SPACING
            ):
                continue

            selected.append(event)
            last_time = event["timestamp"]

    return selected


def main():
    print("\n=== JAHVASCRIPT CLOCK-TIME RVOL AUDIT ===")

    require(
        "journal/" in (
            ROOT / ".gitignore"
        ).read_text().splitlines(),
        "Journal must be gitignored"
    )
    require(not OUTPUT.exists(), "Output already exists")

    dataset_bytes = DATASET.read_bytes()
    dataset_hash = hashlib.sha256(
        dataset_bytes
    ).hexdigest()

    dataset = json.loads(dataset_bytes)
    research_bytes = RESEARCH.read_bytes()
    research = json.loads(research_bytes)

    require(
        dataset.get("dataset_version") == "M5_SPY_QQQ_v001"
        and dataset.get("historical_research_only") is True
        and dataset.get("execution_enabled") is False
        and dataset.get("trade_authorization") is False
        and research.get("dataset_sha256") == dataset_hash
        and research.get("out_of_sample") is False
        and research.get("execution_enabled") is False
        and research.get("trade_authorization") is False
        and research.get("confirmation_horizon_bars") == HORIZON,
        "Unexpected research configuration"
    )

    days = sorted(dataset["full_standard_session_dates"])
    require(len(days) == 17, "Unexpected session count")

    eligible = []
    excluded_horizon = 0
    excluded_warmup = 0
    original_events = 0

    for symbol in ("SPY", "QQQ"):
        sessions = defaultdict(list)

        for bar in dataset["symbols"][symbol]["bars"]:
            dt = datetime.fromisoformat(bar["time"])
            require(
                dt.tzinfo is not None
                and dt.utcoffset() is not None,
                "Missing timestamp timezone"
            )

            day = dt.astimezone(ET).date().isoformat()

            if day in days:
                volume = Decimal(str(bar["volume"]))

                require(
                    volume.is_finite() and volume >= 0,
                    "Invalid volume"
                )

                sessions[day].append({
                    "timestamp": dt,
                    "slot": dt.astimezone(ET).strftime("%H:%M"),
                    "volume": volume
                })

        lookup = {}
        slot_volume = {}

        for day in days:
            rows = sessions[day]

            require(
                len(rows) == 78
                and rows[0]["slot"] == "09:30"
                and rows[-1]["slot"] == "15:55",
                symbol + ": incomplete standard session"
            )

            require(
                all(
                    b["timestamp"] - a["timestamp"]
                    == timedelta(minutes=5)
                    for a, b in zip(rows, rows[1:])
                ),
                symbol + ": session timestamp gap"
            )

            slot_volume[day] = {
                row["slot"]: row["volume"]
                for row in rows
            }

            require(
                len(slot_volume[day]) == 78,
                symbol + ": duplicated clock slot"
            )

            for index, row in enumerate(rows):
                key = row["timestamp"].isoformat()
                require(key not in lookup, "Duplicate timestamp")
                lookup[key] = (day, index, rows)

        for event in research["symbols"][symbol]["events"]:
            original_events += 1

            require(
                event["symbol"] == symbol
                and event["trade_signal"] is False
                and event["direction"] in ("UP", "DOWN")
                and event["outcome"] in (
                    CONFIRMED, SURVIVED, PENDING
                ),
                "Unexpected event metadata"
            )

            dt = datetime.fromisoformat(
                event["breakout_utc"]
            )
            key = dt.isoformat()

            require(key in lookup, "Unknown breakout candle")

            day, index, rows = lookup[key]
            require(
                day == event["date"] and index >= 12,
                "Unexpected event position"
            )

            # Require a complete future confirmation
            # window for every analyzed event.
            if index + HORIZON >= len(rows):
                excluded_horizon += 1
                continue

            require(
                event["outcome"] != PENDING,
                "Pending outcome with full horizon"
            )

            # Original RVOL: prior five 5-minute bars.
            previous = [
                row["volume"]
                for row in rows[index-5:index]
            ]
            rolling_baseline = (
                sum(previous, Decimal(0))
                / Decimal(5)
            )

            require(
                rolling_baseline > 0,
                "Original RVOL baseline unavailable"
            )

            volume = rows[index]["volume"]
            rolling_rvol = volume / rolling_baseline

            require(
                event["rvol"] is not None
                and abs(
                    rolling_rvol
                    - Decimal(str(event["rvol"]))
                ) <= Decimal("0.000051"),
                "Original RVOL could not be reproduced"
            )

            day_index = days.index(day)

            # The first five complete sessions supply
            # historical baselines only.
            if day_index < BASELINE_DAYS:
                excluded_warmup += 1
                continue

            prior_days = days[
                day_index-BASELINE_DAYS:day_index
            ]

            require(
                len(prior_days) == BASELINE_DAYS
                and all(d < day for d in prior_days),
                "Look-ahead in baseline selection"
            )

            slot = rows[index]["slot"]

            reference_volumes = [
                slot_volume[prior_day][slot]
                for prior_day in prior_days
            ]

            clock_baseline = (
                sum(reference_volumes, Decimal(0))
                / Decimal(BASELINE_DAYS)
            )

            require(
                clock_baseline > 0,
                "Clock-time baseline unavailable"
            )

            clock_rvol = volume / clock_baseline

            eligible.append({
                "symbol": symbol,
                "day": day,
                "timestamp": dt,
                "cohort": event["cohort"],
                "direction": event["direction"],
                "outcome": event["outcome"],
                "rolling_group": group(rolling_rvol),
                "clock_group": group(clock_rvol),
                "rolling_rvol": round(float(rolling_rvol), 4),
                "clock_rvol": round(float(clock_rvol), 4),
                "reference_days": prior_days
            })

    require(
        original_events == 328
        and excluded_horizon == 23
        and len(eligible) + excluded_horizon
            + excluded_warmup == original_events,
        "Event totals failed reconciliation"
    )

    print("Original breakout attempts:", original_events)
    print("Excluded incomplete horizons:", excluded_horizon)
    print("Excluded baseline warm-up:", excluded_warmup)
    print("Eligible matched events:", len(eligible))
    print("Historical baseline sessions:", BASELINE_DAYS)
    print(
        "First eligible complete session:",
        days[BASELINE_DAYS]
    )

    results = {
        "overall": display("ALL ELIGIBLE", eligible),
        "by_period": {},
        "by_symbol": {},
        "by_direction": {},
        "clusters": {}
    }

    for cohort in (
        "EARLIER_BACKFILL_EXPLORATORY",
        "PREVIOUSLY_EXAMINED_PERIOD"
    ):
        subset = [
            e for e in eligible if e["cohort"] == cohort
        ]
        results["by_period"][cohort] = display(
            "PERIOD " + cohort, subset
        )

    for symbol in ("SPY", "QQQ"):
        subset = [
            e for e in eligible if e["symbol"] == symbol
        ]
        results["by_symbol"][symbol] = display(
            "SYMBOL " + symbol, subset
        )

    for direction in ("UP", "DOWN"):
        subset = [
            e for e in eligible
            if e["direction"] == direction
        ]
        results["by_direction"][direction] = display(
            "DIRECTION " + direction, subset
        )

    for preferred in ("SPY", "QQQ"):
        selected = cluster(eligible, preferred)
        results["clusters"][preferred] = display(
            preferred + "-FIRST CROSS-MARKET CLUSTERS",
            selected
        )

    changed = sum(
        e["rolling_group"] != e["clock_group"]
        for e in eligible
    )

    print("\nVOLUME CLASSIFICATION COMPARISON")
    print("Events changing RVOL category:", changed)
    print(
        "Events remaining in same category:",
        len(eligible) - changed
    )

    report = {
        "generated_utc": datetime.now(
            timezone.utc
        ).isoformat(),
        "dataset_sha256": dataset_hash,
        "research_sha256": hashlib.sha256(
            research_bytes
        ).hexdigest(),
        "original_events": original_events,
        "excluded_incomplete_horizon": excluded_horizon,
        "excluded_baseline_warmup": excluded_warmup,
        "eligible_events": len(eligible),
        "baseline": "Previous 5 complete sessions, same clock slot",
        "threshold": str(THRESHOLD),
        "classification_changes": changed,
        "results": results,
        "events": [
            {
                **{
                    k: v for k, v in event.items()
                    if k != "timestamp"
                },
                "timestamp": event["timestamp"].isoformat()
            }
            for event in eligible
        ],
        "out_of_sample": False,
        "statistical_independence_established": False,
        "predictive_validity_established": False,
        "source_prices_verified": False,
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

    require(
        hashlib.sha256(DATASET.read_bytes()).hexdigest()
        == dataset_hash
        and hashlib.sha256(RESEARCH.read_bytes()).hexdigest()
        == report["research_sha256"],
        "Source files changed"
    )

    print("\nOriginal research files: UNCHANGED")
    print("No future sessions used in volume baselines: PASS")
    print("Both RVOL methods compared on identical events: PASS")
    print("Clock-time volume pattern: EXPLORATORY")
    print("Out-of-sample predictive test: NONE")
    print("Saved: rvol_clock_normalized_v001.json")

    print("\nCLOCK-TIME RVOL AUDIT PASSED")
    print("WEBULL REQUESTS: NONE")
    print("AI REQUESTS: NONE")
    print("ORDER EXECUTION: DISABLED")


if __name__ == "__main__":
    main()
