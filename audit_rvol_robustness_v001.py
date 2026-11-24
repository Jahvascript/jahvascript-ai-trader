"""JahvaScript RVOL robustness audit.
Offline descriptive analysis; never authorizes trades.
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
OUTPUT = JOURNAL / "rvol_robustness_v001.json"

ET = ZoneInfo("America/New_York")
HIGH_THRESHOLD = Decimal("1.2")
HORIZON = 3
SPACING = timedelta(minutes=15)

CONFIRMED = "CONFIRMED_REVERSAL"
SURVIVED = "NO_FAILURE_WITHIN_HORIZON"
PENDING = "PENDING"


def require(ok, message):
    if not ok:
        raise ValueError("AUDIT STOPPED: " + message)


def summarize(events):
    groups = {}

    for name in ("HIGH", "LOW_NORMAL"):
        rows = [e for e in events if e["volume_group"] == name]
        failed = sum(e["outcome"] == CONFIRMED for e in rows)

        groups[name] = {
            "reversed": failed,
            "resolved": len(rows),
            "percentage": (
                round(100 * failed / len(rows), 1)
                if rows else None
            )
        }

    high = groups["HIGH"]["percentage"]
    lower = groups["LOW_NORMAL"]["percentage"]

    return {
        "events": len(events),
        "groups": groups,
        "lower_minus_high_points": (
            round(lower - high, 1)
            if high is not None and lower is not None
            else None
        )
    }


def display(label, result):
    print("\n" + label)
    print("Resolved events:", result["events"])

    for name, values in result["groups"].items():
        pct = values["percentage"]
        shown = f"{pct:.1f}%" if pct is not None else "N/A"

        print(
            f"  {name}: {values['reversed']}/"
            f"{values['resolved']} ({shown})"
        )

    print(
        "  LOW_NORMAL minus HIGH:",
        result["lower_minus_high_points"],
        "percentage points"
    )


def cluster(events, preferred):
    days = defaultdict(list)

    for event in events:
        days[event["date"]].append(event)

    chosen = []

    for day, rows in sorted(days.items()):
        ordered = sorted(
            rows,
            key=lambda e: (
                e["timestamp"],
                e["symbol"] != preferred
            )
        )

        last = None

        for event in ordered:
            if (
                last is not None
                and event["timestamp"] - last <= SPACING
            ):
                continue

            chosen.append(event)
            last = event["timestamp"]

    return chosen


def main():
    print("\n=== JAHVASCRIPT RVOL ROBUSTNESS AUDIT ===")

    require(
        "journal/" in (ROOT / ".gitignore").read_text().splitlines(),
        "Journal must be gitignored"
    )
    require(not OUTPUT.exists(), "Audit report already exists")

    dataset_bytes = DATASET.read_bytes()
    dataset_hash = hashlib.sha256(dataset_bytes).hexdigest()

    dataset = json.loads(dataset_bytes)
    research = json.loads(RESEARCH.read_text())

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
        "Unexpected dataset/research configuration"
    )

    complete_dates = set(
        dataset["full_standard_session_dates"]
    )

    eligible = []
    excluded = []
    total = 0

    for symbol in ("SPY", "QQQ"):
        sessions = defaultdict(list)

        for bar in dataset["symbols"][symbol]["bars"]:
            dt = datetime.fromisoformat(bar["time"])
            require(dt.tzinfo is not None, "Unzoned bar")
            date = dt.astimezone(ET).date().isoformat()

            sessions[date].append(bar)

        lookup = {}

        for day in complete_dates:
            bars = sessions[day]
            require(len(bars) == 78, "Incomplete session")

            for i, bar in enumerate(bars):
                dt = datetime.fromisoformat(bar["time"])
                key = dt.astimezone(timezone.utc).isoformat()
                require(key not in lookup, "Duplicate bar")
                lookup[key] = (day, i, bars)

        for event in research["symbols"][symbol]["events"]:
            total += 1

            timestamp = datetime.fromisoformat(
                event["breakout_utc"]
            )
            require(
                timestamp.tzinfo is not None
                and event["symbol"] == symbol
                and event["trade_signal"] is False,
                "Unsafe or unzoned event"
            )

            key = timestamp.astimezone(
                timezone.utc
            ).isoformat()

            require(key in lookup, "Unknown breakout candle")

            day, index, bars = lookup[key]

            require(
                day == event["date"]
                and index >= 12
                and event["direction"] in ("UP", "DOWN")
                and event["outcome"] in (
                    CONFIRMED, SURVIVED, PENDING
                ),
                "Unexpected breakout metadata"
            )

            baseline = sum(
                (
                    Decimal(str(b["volume"]))
                    for b in bars[index-5:index]
                ),
                Decimal(0)
            ) / Decimal(5)

            volume = Decimal(str(bars[index]["volume"]))

            require(
                baseline.is_finite()
                and volume.is_finite()
                and baseline >= 0
                and volume >= 0,
                "Invalid volume"
            )

            exact_rvol = (
                volume / baseline if baseline > 0 else None
            )

            reported = event["rvol"]

            if exact_rvol is None:
                require(reported is None, "Unexpected saved RVOL")
                volume_group = "UNAVAILABLE"
            else:
                require(
                    reported is not None
                    and abs(
                        Decimal(str(reported)) - exact_rvol
                    ) <= Decimal("0.000051"),
                    "Saved RVOL differs from raw volume"
                )

                volume_group = (
                    "HIGH" if exact_rvol >= HIGH_THRESHOLD
                    else "LOW_NORMAL"
                )

            item = {
                "symbol": symbol,
                "date": day,
                "timestamp": timestamp,
                "cohort": event["cohort"],
                "direction": event["direction"],
                "outcome": event["outcome"],
                "volume_group": volume_group
            }

            # For unbiased horizon availability, require
            # three later bars regardless of early failure.
            if index + HORIZON >= len(bars):
                excluded.append(item)
                continue

            require(
                event["outcome"] != PENDING,
                "Pending event despite full horizon"
            )

            if volume_group != "UNAVAILABLE":
                eligible.append(item)
            else:
                excluded.append(item)

    require(total == 328, "Unexpected original event count")

    print("Original breakout attempts:", total)
    print("Excluded events:", len(excluded))
    print(
        "Excluded pending:",
        sum(e["outcome"] == PENDING for e in excluded)
    )
    print(
        "Excluded already-confirmed reversals:",
        sum(e["outcome"] == CONFIRMED for e in excluded)
    )
    print(
        "Excluded without RVOL:",
        sum(
            e["volume_group"] == "UNAVAILABLE"
            for e in excluded
        )
    )

    require(
        len(eligible) + len(excluded) == total,
        "Event reconciliation failed"
    )

    results = {}

    comparisons = [
        ("ALL_ELIGIBLE", eligible)
    ]

    for cohort in (
        "EARLIER_BACKFILL_EXPLORATORY",
        "PREVIOUSLY_EXAMINED_PERIOD"
    ):
        comparisons.append((
            cohort,
            [e for e in eligible if e["cohort"] == cohort]
        ))

    for symbol in ("SPY", "QQQ"):
        comparisons.append((
            symbol,
            [e for e in eligible if e["symbol"] == symbol]
        ))

    for direction in ("UP", "DOWN"):
        comparisons.append((
            direction,
            [e for e in eligible if e["direction"] == direction]
        ))

    print("\n--- UNCLUSTERED COMPARISONS ---")

    for name, rows in comparisons:
        result = summarize(rows)
        display(name, result)
        results[name] = result

    print("\n--- CROSS-MARKET CLUSTER CHECKS ---")

    cluster_results = {}

    for cohort in (
        "EARLIER_BACKFILL_EXPLORATORY",
        "PREVIOUSLY_EXAMINED_PERIOD"
    ):
        subset = [
            e for e in eligible if e["cohort"] == cohort
        ]

        for preferred in ("SPY", "QQQ"):
            selected = cluster(subset, preferred)
            name = cohort + "_" + preferred + "_FIRST"

            result = summarize(selected)
            display(name, result)
            cluster_results[name] = result

    report = {
        "generated_utc": datetime.now(
            timezone.utc
        ).isoformat(),
        "dataset_sha256": dataset_hash,
        "original_breakouts": total,
        "eligible_events": len(eligible),
        "excluded_events": len(excluded),
        "excluded_outcomes": dict(
            Counter(e["outcome"] for e in excluded)
        ),
        "volume_threshold": str(HIGH_THRESHOLD),
        "minimum_forward_candles": HORIZON,
        "unclustered": results,
        "clustered": cluster_results,
        "research_only": True,
        "out_of_sample": False,
        "price_source_independently_verified": False,
        "predictive_validity_established": False,
        "trade_signal": False,
        "execution_enabled": False,
        "trade_authorization": False
    }

    os.umask(0o077)

    with OUTPUT.open("x") as handle:
        json.dump(report, handle, indent=2)
        handle.write("\n")

    OUTPUT.chmod(0o600)

    print("\nAUDIT LIMITATIONS")
    print("Volume thresholds were chosen during exploration")
    print("Temporal clustering does not prove independence")
    print("No confidence intervals or predictive claims")
    print("Out-of-sample validation: NOT PERFORMED")
    print("Source prices independently verified: NO")
    print("Original reports and datasets: UNCHANGED")
    print("Saved: rvol_robustness_v001.json")
    print("\nRVOL ROBUSTNESS AUDIT PASSED")
    print("WEBULL REQUESTS: NONE")
    print("AI REQUESTS: NONE")
    print("ORDER EXECUTION: DISABLED")


if __name__ == "__main__":
    main()
