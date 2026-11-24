"""JahvaScript cross-market breakout cluster audit.
Offline historical sensitivity analysis. No trading.
"""

import json
import os
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parent
SOURCE = ROOT / "journal/historical_trap_context.json"
OUTPUT = ROOT / "journal/trap_cluster_audit.json"

ET = ZoneInfo("America/New_York")
SPACING_SECONDS = 15 * 60
PENDING = "AWAITING_MORE_CANDLES"
CONFIRMED = "CONFIRMED_TRAP"


def require(condition, message):
    if not condition:
        raise ValueError("STOP: " + message)


def rate(events, direction):
    resolved = [
        e for e in events
        if e["direction"] == direction
        and e["outcome"] != PENDING
    ]

    reversals = sum(
        e["outcome"] == CONFIRMED
        for e in resolved
    )

    return {
        "reversals": reversals,
        "resolved": len(resolved),
        "percentage": (
            round(100 * reversals / len(resolved), 1)
            if resolved else None
        )
    }


def select_spaced(events, preferred_symbol):
    """Select earliest event, then require >15m spacing.

    Tie-break by symbol only, never by outcome.
    """

    by_day = defaultdict(list)

    for event in events:
        by_day[event["day"]].append(event)

    selected = []

    for day, rows in sorted(by_day.items()):
        rows.sort(
            key=lambda e: (
                e["timestamp"],
                e["symbol"] != preferred_symbol
            )
        )

        last_selected = None

        for event in rows:
            if (
                last_selected is not None
                and (
                    event["timestamp"] - last_selected
                ).total_seconds() <= SPACING_SECONDS
            ):
                continue

            selected.append(event)
            last_selected = event["timestamp"]

    return selected


def show_rate(label, result):
    percentage = result["percentage"]

    if percentage is None:
        print(label + ": N/A")
    else:
        print(
            f"{label}: {result['reversals']}/"
            f"{result['resolved']} "
            f"({percentage:.1f}%)"
        )


def main():
    require(SOURCE.is_file(), "Context report missing")

    require(
        "journal/" in (
            ROOT / ".gitignore"
        ).read_text().splitlines(),
        "Journal must be gitignored"
    )

    source = json.loads(SOURCE.read_text())

    require(
        source.get("historical_research_only") is True
        and source.get("execution_enabled") is False
        and source.get("trade_authorization") is False,
        "Unsafe source"
    )

    events = []
    unique = set()

    for symbol in ("SPY", "QQQ"):
        for item in source["symbols"][symbol]["events"]:
            dt = datetime.fromisoformat(
                item["breakout_time_utc"]
            )

            require(
                dt.tzinfo is not None
                and dt.utcoffset() is not None,
                "Timezone missing"
            )

            day = dt.astimezone(ET).date().isoformat()

            require(
                day == item["session_date"],
                "Session date mismatch"
            )
            require(
                item["trade_signal"] is False
                and item["research_only"] is True,
                "Unsafe event"
            )
            require(
                item["direction"] in ("UP", "DOWN"),
                "Unknown direction"
            )
            require(
                item["outcome"] in (
                    CONFIRMED,
                    "NO_FAILURE_WITHIN_HORIZON",
                    PENDING
                ),
                "Unknown outcome"
            )

            key = (symbol, dt)
            require(key not in unique, "Duplicate event")
            unique.add(key)

            events.append({
                "symbol": symbol,
                "day": day,
                "timestamp": dt,
                "direction": item["direction"],
                "outcome": item["outcome"]
            })

    print("\n=== JAHVASCRIPT CROSS-MARKET CLUSTER AUDIT ===")
    print("Original events:", len(events))
    print("Trading dates:", len(set(e["day"] for e in events)))

    print("\nORIGINAL SAMPLE")
    for direction in ("UP", "DOWN"):
        show_rate(direction, rate(events, direction))

    results = {}

    for preferred in ("SPY", "QQQ"):
        selected = select_spaced(events, preferred)

        # Verify spacing across BOTH symbols,
        # not separately for SPY and QQQ.
        for day in set(e["day"] for e in selected):
            times = sorted(
                e["timestamp"]
                for e in selected
                if e["day"] == day
            )

            require(
                all(
                    (b - a).total_seconds()
                    > SPACING_SECONDS
                    for a, b in zip(times, times[1:])
                ),
                "Overlapping representatives"
            )

        up = rate(selected, "UP")
        down = rate(selected, "DOWN")

        difference = (
            round(
                down["percentage"] - up["percentage"],
                1
            )
            if (
                down["percentage"] is not None
                and up["percentage"] is not None
            )
            else None
        )

        print("\n" + preferred + "-FIRST SELECTION")
        print("Representatives:", len(selected))
        print("Events excluded:", len(events) - len(selected))
        print(
            "Pending representatives:",
            sum(e["outcome"] == PENDING for e in selected)
        )
        show_rate("UP", up)
        show_rate("DOWN", down)
        print(
            "DOWN minus UP:",
            difference,
            "percentage points"
        )

        print("REPRESENTATIVES BY DAY")
        counts = Counter(e["day"] for e in selected)
        for day, count in sorted(counts.items()):
            print(day + ":", count)

        results[preferred] = {
            "representatives": len(selected),
            "pending": sum(
                e["outcome"] == PENDING for e in selected
            ),
            "up": up,
            "down": down,
            "down_minus_up_points": difference,
            "daily_counts": dict(counts)
        }

    report = {
        "generated_utc": datetime.now(
            timezone.utc
        ).isoformat(),
        "original_events": len(events),
        "minimum_representative_spacing_minutes": 15,
        "selection_basis": (
            "Earliest chronological event each interval; "
            "same-timestamp ties resolved by chosen symbol. "
            "Outcomes not used for selection."
        ),
        "results": results,
        "independent_observations_established": False,
        "predictive_validity_established": False,
        "source_prices_verified": False,
        "historical_research_only": True,
        "trade_signal": False,
        "execution_enabled": False,
        "trade_authorization": False
    }

    os.umask(0o077)
    OUTPUT.write_text(
        json.dumps(report, indent=2) + "\n"
    )
    OUTPUT.chmod(0o600)

    print("\nINTERPRETATION LIMITS")
    print("Events separated in time, not statistically independent")
    print("Same-day market regimes may still create dependence")
    print("Representative selection is a sensitivity test")
    print("Source prices independently verified: NO")
    print("Out-of-sample prediction tested: NO")

    print("\nSaved: trap_cluster_audit.json")
    print("\nCROSS-MARKET CLUSTER AUDIT PASSED")
    print("AI REQUESTS: NONE")
    print("WEBULL REQUESTS: NONE")
    print("ORDER EXECUTION: DISABLED")


if __name__ == "__main__":
    main()
