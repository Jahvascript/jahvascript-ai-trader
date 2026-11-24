"""JahvaScript RVOL method agreement audit.
Offline historical research. No APIs or orders.
"""

import hashlib
import json
import os
from collections import Counter, defaultdict
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path

ROOT = Path(__file__).resolve().parent
JOURNAL = ROOT / "journal"
SOURCE = JOURNAL / "rvol_clock_normalized_v001.json"
DATASET = JOURNAL / "datasets/M5_SPY_QQQ_v001.json"
OUTPUT = JOURNAL / "rvol_agreement_v001.json"

THRESHOLD = Decimal("1.2")
LABELS = {
    ("HIGH", "HIGH"): "HIGH_TO_HIGH",
    ("HIGH", "LOW_NORMAL"): "HIGH_TO_LOW_NORMAL",
    ("LOW_NORMAL", "HIGH"): "LOW_NORMAL_TO_HIGH",
    ("LOW_NORMAL", "LOW_NORMAL"): "LOW_NORMAL_TO_LOW_NORMAL",
}


def require(ok, message):
    if not ok:
        raise ValueError("AUDIT STOPPED: " + message)


def summarize(rows):
    reversed_count = sum(
        e["outcome"] == "CONFIRMED_REVERSAL"
        for e in rows
    )
    return {
        "events": len(rows),
        "reversed": reversed_count,
        "no_failure": len(rows) - reversed_count,
        "reversal_percentage": (
            round(100 * reversed_count / len(rows), 1)
            if rows else None
        )
    }


def print_group(label, rows):
    result = summarize(rows)
    pct = result["reversal_percentage"]
    display = f"{pct:.1f}%" if pct is not None else "N/A"

    print(
        f"{label}: {result['events']} events | "
        f"{result['reversed']} reversals ({display})"
    )
    if len(rows) < 10:
        print("  SMALL SAMPLE — interpret cautiously")

    return result


def main():
    print("\n=== JAHVASCRIPT RVOL AGREEMENT AUDIT ===")

    require(
        "journal/" in (
            ROOT / ".gitignore"
        ).read_text().splitlines(),
        "Journal must be gitignored"
    )
    require(not OUTPUT.exists(), "Audit report already exists")

    source_bytes = SOURCE.read_bytes()
    source = json.loads(source_bytes)
    dataset_hash = hashlib.sha256(
        DATASET.read_bytes()
    ).hexdigest()

    require(
        source.get("dataset_sha256") == dataset_hash
        and source.get("historical_research_only") is True
        and source.get("out_of_sample") is False
        and source.get("execution_enabled") is False
        and source.get("trade_authorization") is False
        and source.get("trade_signal") is False
        and source.get("predictive_validity_established") is False,
        "Unexpected source configuration"
    )

    original = source["events"]
    require(
        len(original) == 239
        and source.get("eligible_events") == 239
        and source.get("classification_changes") == 87,
        "Unexpected original event totals"
    )

    events = []
    seen = set()

    for item in original:
        require(
            item["symbol"] in ("SPY", "QQQ")
            and item["direction"] in ("UP", "DOWN")
            and item["outcome"] in (
                "CONFIRMED_REVERSAL",
                "NO_FAILURE_WITHIN_HORIZON"
            )
            and item["cohort"] in (
                "EARLIER_BACKFILL_EXPLORATORY",
                "PREVIOUSLY_EXAMINED_PERIOD"
            ),
            "Unexpected event values"
        )

        dt = datetime.fromisoformat(item["timestamp"])
        require(
            dt.tzinfo is not None
            and dt.utcoffset() is not None,
            "Missing timestamp timezone"
        )

        key = (item["symbol"], dt)
        require(key not in seen, "Duplicate event")
        seen.add(key)

        reference_days = item["reference_days"]

        require(
            len(reference_days) == 5
            and len(set(reference_days)) == 5
            and reference_days == sorted(reference_days)
            and all(day < item["day"] for day in reference_days),
            "Invalid historical reference days"
        )

        rolling = Decimal(str(item["rolling_rvol"]))
        clock = Decimal(str(item["clock_rvol"]))

        require(
            rolling.is_finite() and clock.is_finite()
            and rolling >= 0 and clock >= 0,
            "Invalid relative volume"
        )

        pair = (
            item["rolling_group"],
            item["clock_group"]
        )

        require(pair in LABELS, "Unknown RVOL classifications")

        # RVOL values are stored rounded to 4 decimals.
        # Near 1.2, keep the original unrounded grouping
        # instead of falsely flagging a rounding mismatch.
        for value, category in (
            (rolling, pair[0]),
            (clock, pair[1])
        ):
            if abs(value - THRESHOLD) > Decimal("0.00005"):
                expected = (
                    "HIGH" if value >= THRESHOLD
                    else "LOW_NORMAL"
                )
                require(
                    category == expected,
                    "RVOL category/value inconsistency"
                )

        events.append({
            "symbol": item["symbol"],
            "day": item["day"],
            "direction": item["direction"],
            "cohort": item["cohort"],
            "outcome": item["outcome"],
            "transition": LABELS[pair]
        })

    changed = sum(
        e["transition"] in (
            "HIGH_TO_LOW_NORMAL",
            "LOW_NORMAL_TO_HIGH"
        )
        for e in events
    )

    require(
        changed == 87,
        "Classification-change count mismatch"
    )

    print("Matched events:", len(events))
    print("Agreement count:", len(events) - changed)
    print("Disagreement count:", changed)
    print(
        "Disagreement percentage:",
        f"{100 * changed / len(events):.1f}%"
    )

    print("\n--- RVOL TRANSITION MATRIX ---")
    results = {"transitions": {}, "by_symbol": {},
               "by_direction": {}, "by_period": {}}

    for name in LABELS.values():
        rows = [e for e in events if e["transition"] == name]
        results["transitions"][name] = print_group(name, rows)

    for field, destination in (
        ("symbol", "by_symbol"),
        ("direction", "by_direction"),
        ("cohort", "by_period")
    ):
        print("\n---", destination.upper(), "---")

        for value in sorted(set(e[field] for e in events)):
            subset = [e for e in events if e[field] == value]
            disagreement = [
                e for e in subset
                if e["transition"] in (
                    "HIGH_TO_LOW_NORMAL",
                    "LOW_NORMAL_TO_HIGH"
                )
            ]

            print(
                value,
                "| total:", len(subset),
                "| category changes:", len(disagreement),
                f"({100 * len(disagreement) / len(subset):.1f}%)"
            )

            results[destination][value] = {
                "total": len(subset),
                "changed": len(disagreement),
                "transitions": {
                    name: summarize([
                        e for e in subset
                        if e["transition"] == name
                    ])
                    for name in LABELS.values()
                }
            }

    require(
        sum(
            item["events"]
            for item in results["transitions"].values()
        ) == 239,
        "Transition totals do not reconcile"
    )

    report = {
        "generated_utc": datetime.now(
            timezone.utc
        ).isoformat(),
        "source_sha256": hashlib.sha256(
            source_bytes
        ).hexdigest(),
        "dataset_sha256": dataset_hash,
        "matched_events": len(events),
        "classification_changes": changed,
        "results": results,
        "method_threshold": "RVOL >= 1.2",
        "outcomes_are_retrospective": True,
        "original_groups_retained_near_rounding_boundary": True,
        "out_of_sample": False,
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
        hashlib.sha256(SOURCE.read_bytes()).hexdigest()
        == report["source_sha256"],
        "Source changed during audit"
    )

    print("\nRVOL disagreement accounting: PASS")
    print("Historical reference-day ordering: PASS")
    print("Original datasets and reports: UNCHANGED")
    print("Predictive accuracy: NOT ESTABLISHED")
    print("Out-of-sample test: NONE")
    print("Saved: rvol_agreement_v001.json")

    print("\nRVOL AGREEMENT AUDIT PASSED")
    print("WEBULL REQUESTS: NONE")
    print("AI REQUESTS: NONE")
    print("ORDER EXECUTION: DISABLED")


if __name__ == "__main__":
    main()
