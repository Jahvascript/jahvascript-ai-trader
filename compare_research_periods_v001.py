"""JahvaScript V001 historical period comparison.
Offline, descriptive research only. No AI, APIs or orders.
"""

import hashlib
import json
import math
import os
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parent
JOURNAL = ROOT / "journal"
DATASET = JOURNAL / "datasets/M5_SPY_QQQ_v001.json"
SOURCE = JOURNAL / "expanded_research_v001.json"
OUTPUT = JOURNAL / "research_period_comparison_v001.json"

ET = ZoneInfo("America/New_York")
CONFIRMED = "CONFIRMED_REVERSAL"
SURVIVED = "NO_FAILURE_WITHIN_HORIZON"
PENDING = "PENDING"

EARLIER = "EARLIER_BACKFILL_EXPLORATORY"
LATER = "PREVIOUSLY_EXAMINED_PERIOD"
COHORTS = (EARLIER, LATER)
SPACING_SECONDS = 15 * 60


def require(ok, message):
    if not ok:
        raise ValueError("RESEARCH STOPPED: " + message)


def rate(events):
    resolved = [
        e for e in events if e["outcome"] != PENDING
    ]

    failed = sum(
        e["outcome"] == CONFIRMED for e in resolved
    )

    return {
        "reversed": failed,
        "resolved": len(resolved),
        "pending": sum(e["outcome"] == PENDING for e in events),
        "percent": (
            round(100 * failed / len(resolved), 1)
            if resolved else None
        )
    }


def show(label, result):
    percentage = result["percent"]
    display = (
        f"{percentage:.1f}%"
        if percentage is not None else "N/A"
    )

    print(
        f"{label}: {result['reversed']}/"
        f"{result['resolved']} reversed "
        f"({display}); pending={result['pending']}"
    )


def volume_group(value):
    if value is None:
        return "UNAVAILABLE"

    number = float(value)

    require(
        math.isfinite(number) and number >= 0,
        "Invalid relative volume"
    )

    if number < 0.8:
        return "LOW"
    if number < 1.2:
        return "NORMAL"

    return "HIGH"


def summarize_dimension(events, field, labels):
    results = {}

    for label in labels:
        subset = [
            e for e in events if e[field] == label
        ]
        results[label] = rate(subset)

        if subset:
            show("  " + label, results[label])

    return results


def cluster_representatives(events, preferred_symbol):
    """Chronological 15-minute spacing across SPY and QQQ.

    The outcome never determines which event is selected.
    """
    by_day = defaultdict(list)

    for event in events:
        by_day[event["day"]].append(event)

    selected = []

    for day, rows in sorted(by_day.items()):
        ordered = sorted(
            rows,
            key=lambda e: (
                e["timestamp"],
                e["symbol"] != preferred_symbol
            )
        )

        latest = None

        for event in ordered:
            if latest is not None:
                elapsed = (
                    event["timestamp"] - latest
                ).total_seconds()

                if elapsed <= SPACING_SECONDS:
                    continue

            selected.append(event)
            latest = event["timestamp"]

    for day in by_day:
        times = sorted(
            e["timestamp"]
            for e in selected if e["day"] == day
        )

        require(
            all(
                (b - a).total_seconds() > SPACING_SECONDS
                for a, b in zip(times, times[1:])
            ),
            "Cluster spacing failure"
        )

    return selected


def load():
    raw_dataset = DATASET.read_bytes()
    digest = hashlib.sha256(raw_dataset).hexdigest()

    dataset = json.loads(raw_dataset)
    research = json.loads(SOURCE.read_text())

    require(
        dataset.get("dataset_version") == "M5_SPY_QQQ_v001"
        and dataset.get("historical_research_only") is True
        and dataset.get("execution_enabled") is False
        and dataset.get("trade_authorization") is False,
        "Unexpected dataset configuration"
    )

    require(
        research.get("dataset_sha256") == digest
        and research.get("out_of_sample") is False
        and research.get("historical_research_only") is True
        and research.get("execution_enabled") is False
        and research.get("trade_authorization") is False
        and research.get("lookback_bars") == 12
        and research.get("confirmation_horizon_bars") == 3,
        "Unexpected research configuration"
    )

    require(
        len(dataset["full_standard_session_dates"]) == 17,
        "Unexpected complete session count"
    )

    full_dates = set(
        dataset["full_standard_session_dates"]
    )

    events = []
    seen = set()

    for symbol in ("SPY", "QQQ"):
        source_events = research["symbols"][symbol]["events"]

        for original in source_events:
            require(
                original["symbol"] == symbol
                and original["trade_signal"] is False,
                "Unsafe or mismatched event"
            )

            require(
                original["outcome"] in (
                    CONFIRMED, SURVIVED, PENDING
                ),
                "Unrecognized outcome"
            )

            require(
                original["direction"] in ("UP", "DOWN"),
                "Unrecognized direction"
            )

            require(
                original["ema_alignment"] in (
                    "BULLISH_STACK", "BEARISH_STACK",
                    "MIXED", "UNAVAILABLE"
                ),
                "Unexpected EMA alignment"
            )

            require(
                original["vwap_position"] in (
                    "ABOVE", "BELOW", "AT", "UNAVAILABLE"
                ),
                "Unexpected VWAP position"
            )

            timestamp = datetime.fromisoformat(
                original["breakout_utc"]
            )

            require(
                timestamp.tzinfo is not None
                and timestamp.utcoffset() is not None,
                "Missing event timezone"
            )

            day = timestamp.astimezone(
                ET
            ).date().isoformat()

            require(
                day == original["date"]
                and day in full_dates,
                "Invalid session date"
            )

            cohort = (
                EARLIER if day < "2026-10-12" else LATER
            )

            require(
                original["cohort"] == cohort,
                "Incorrect cohort label"
            )

            if original["outcome"] == CONFIRMED:
                require(
                    original["confirmation_utc"] is not None,
                    "Missing confirmation timestamp"
                )

                confirmation = datetime.fromisoformat(
                    original["confirmation_utc"]
                )

                require(
                    confirmation.tzinfo is not None
                    and 0 < (
                        confirmation - timestamp
                    ).total_seconds() <= SPACING_SECONDS,
                    "Invalid confirmation timing"
                )
            else:
                require(
                    original["confirmation_utc"] is None,
                    "Unconfirmed event has confirmation"
                )

            key = (symbol, timestamp)

            require(
                key not in seen,
                "Duplicate breakout event"
            )

            seen.add(key)

            event = {
                "symbol": symbol,
                "day": day,
                "timestamp": timestamp,
                "cohort": cohort,
                "direction": original["direction"],
                "outcome": original["outcome"],
                "ema": original["ema_alignment"],
                "vwap": original["vwap_position"],
                "rvol": volume_group(original["rvol"])
            }

            events.append(event)

        expected = Counter(
            e["outcome"]
            for e in source_events
        )

        require(
            dict(expected)
            == research["symbols"][symbol]["outcomes"],
            symbol + ": outcome totals mismatch"
        )

    counts = Counter(e["outcome"] for e in events)

    require(
        len(events) == 328
        and counts[CONFIRMED] == 155
        and counts[SURVIVED] == 155
        and counts[PENDING] == 18,
        "Expanded research totals changed"
    )

    return events, digest


def main():
    print(
        "\n=== JAHVASCRIPT HISTORICAL PERIOD COMPARISON ==="
    )

    require(
        "journal/" in (
            ROOT / ".gitignore"
        ).read_text().splitlines(),
        "Journal must be gitignored"
    )

    require(
        not OUTPUT.exists(),
        "V001 comparison exists; refusing overwrite"
    )

    events, digest = load()

    print("Dataset: M5_SPY_QQQ_v001")
    print("Historical events:", len(events))
    print("Untouched out-of-sample data: NONE")

    report = {
        "dataset": "M5_SPY_QQQ_v001",
        "dataset_sha256": digest,
        "generated_utc": datetime.now(
            timezone.utc
        ).isoformat(),
        "cohorts": {},
        "historical_exploratory_only": True,
        "independent_observations_assumed": False,
        "predictive_validity_established": False,
        "source_prices_verified": False,
        "trade_signal": False,
        "execution_enabled": False,
        "trade_authorization": False
    }

    for cohort in COHORTS:
        group = [
            e for e in events if e["cohort"] == cohort
        ]

        print("\n---", cohort, "---")
        print("Breakout attempts:", len(group))
        print(
            "Trading dates:",
            len(set(e["day"] for e in group))
        )

        show("OVERALL", rate(group))

        results = {
            "events": len(group),
            "overall": rate(group),
            "directions": {},
            "ema": {},
            "vwap": {},
            "relative_volume": {},
            "cross_market_clusters": {}
        }

        print("\nBY DIRECTION")
        results["directions"] = summarize_dimension(
            group, "direction", ("UP", "DOWN")
        )

        print("\nBY EMA ALIGNMENT")
        results["ema"] = summarize_dimension(
            group, "ema",
            (
                "BULLISH_STACK", "BEARISH_STACK",
                "MIXED", "UNAVAILABLE"
            )
        )

        print("\nBY VWAP POSITION")
        results["vwap"] = summarize_dimension(
            group, "vwap",
            ("ABOVE", "BELOW", "AT", "UNAVAILABLE")
        )

        print("\nBY RELATIVE VOLUME")
        results["relative_volume"] = summarize_dimension(
            group, "rvol",
            ("LOW", "NORMAL", "HIGH", "UNAVAILABLE")
        )

        print("\nCROSS-MARKET 15-MINUTE CLUSTERS")

        for preferred in ("SPY", "QQQ"):
            selected = cluster_representatives(
                group, preferred
            )

            up = rate([
                e for e in selected
                if e["direction"] == "UP"
            ])

            down = rate([
                e for e in selected
                if e["direction"] == "DOWN"
            ])

            difference = (
                round(
                    down["percent"] - up["percent"],
                    1
                )
                if (
                    down["percent"] is not None
                    and up["percent"] is not None
                )
                else None
            )

            print(
                preferred + "-first:",
                len(selected),
                "representatives"
            )
            show("  UP", up)
            show("  DOWN", down)
            print(
                "  DOWN minus UP:",
                difference,
                "percentage points"
            )

            results["cross_market_clusters"][
                preferred
            ] = {
                "representatives": len(selected),
                "up": up,
                "down": down,
                "down_minus_up_points": difference
            }

        report["cohorts"][cohort] = results

    require(
        sum(
            item["events"]
            for item in report["cohorts"].values()
        ) == 328,
        "Cohort reconciliation failed"
    )

    os.umask(0o077)
    OUTPUT.parent.mkdir(parents=True, exist_ok=True)

    with OUTPUT.open("x") as handle:
        json.dump(report, handle, indent=2)
        handle.write("\n")

    OUTPUT.chmod(0o600)

    print("\nLIMITATIONS")
    print("Cohorts: BOTH EXPLORATORY")
    print("SPY/QQQ dependence: NOT ELIMINATED")
    print("15-minute cluster spacing: SENSITIVITY TEST")
    print("Feature relationships may be confounded")
    print("Source prices independently verified: NO")
    print("Predictive trading edge established: NO")
    print("Original research reports: UNCHANGED")
    print("Saved: research_period_comparison_v001.json")

    print("\nHISTORICAL PERIOD COMPARISON PASSED")
    print("WEBULL REQUESTS: NONE")
    print("AI REQUESTS: NONE")
    print("ORDER EXECUTION: DISABLED")


if __name__ == "__main__":
    main()
