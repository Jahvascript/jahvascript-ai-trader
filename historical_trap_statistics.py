"""JahvaScript Historical Trap Statistics.
Offline descriptive research. No signals or orders.
"""

import json
import math
import os
from collections import defaultdict, Counter
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent
SOURCE = ROOT / "journal/historical_trap_context.json"
OUTPUT = ROOT / "journal/historical_trap_statistics.json"

CONFIRMED = "CONFIRMED_TRAP"
SURVIVED = "NO_FAILURE_WITHIN_HORIZON"
PENDING = "AWAITING_MORE_CANDLES"

OUTCOMES = {CONFIRMED, SURVIVED, PENDING}


def require(ok, message):
    if not ok:
        raise ValueError("STOP: " + message)


def volume_bucket(value):
    if value is None:
        return "UNAVAILABLE"

    value = float(value)

    require(
        math.isfinite(value) and value >= 0,
        "Invalid relative volume"
    )

    if value < 0.8:
        return "LOW_RVOL"
    if value < 1.2:
        return "NORMAL_RVOL"

    return "HIGH_RVOL"


def summarize(events, fields):
    groups = defaultdict(Counter)

    for event in events:
        key = tuple(event[field] for field in fields)
        groups[key][event["outcome"]] += 1

    results = []

    for key, counts in groups.items():
        failures = counts[CONFIRMED]
        survived = counts[SURVIVED]
        pending = counts[PENDING]
        resolved = failures + survived

        results.append({
            "group": dict(zip(fields, key)),
            "resolved": resolved,
            "confirmed_reversals": failures,
            "no_failure_within_horizon": survived,
            "pending_excluded": pending,
            "reversal_percentage": (
                round(failures / resolved * 100, 1)
                if resolved else None
            )
        })

    return sorted(
        results,
        key=lambda row: (
            -row["resolved"],
            str(row["group"])
        )
    )


def print_table(title, rows, minimum=1):
    print("\n" + title)

    shown = 0

    for row in rows:
        if row["resolved"] < minimum:
            continue

        label = " / ".join(
            str(value) for value in row["group"].values()
        )

        rate = row["reversal_percentage"]

        display = (
            f"{rate:.1f}%"
            if rate is not None else "N/A"
        )

        print(
            f"{label}: "
            f"{row['confirmed_reversals']}/"
            f"{row['resolved']} reversed "
            f"({display}), "
            f"pending={row['pending_excluded']}"
        )

        shown += 1

    if not shown:
        print("No groups meet minimum sample size")


def main():
    require(SOURCE.is_file(), "Context report missing")

    require(
        "journal/" in (
            ROOT / ".gitignore"
        ).read_text().splitlines(),
        "Journal not gitignored"
    )

    data = json.loads(SOURCE.read_text())

    require(
        data.get("historical_research_only") is True
        and data.get("outcomes_known_only_after_confirmation") is True
        and data.get("live_data_verified") is False
        and data.get("execution_enabled") is False
        and data.get("trade_authorization") is False,
        "Unsafe research input"
    )

    all_events = []
    symbols = {}
    seen = set()

    for symbol in ("SPY", "QQQ"):
        events = data["symbols"][symbol]["events"]
        counts = Counter()

        for original in events:
            event = dict(original)

            require(
                event["outcome"] in OUTCOMES,
                "Unknown breakout outcome"
            )
            require(
                event["direction"] in ("UP", "DOWN"),
                "Unknown breakout direction"
            )
            require(
                event["ema_alignment"] in (
                    "BULLISH_STACK", "BEARISH_STACK",
                    "MIXED", "UNAVAILABLE"
                ),
                "Unknown EMA alignment"
            )
            require(
                event["vwap_position"] in (
                    "ABOVE", "BELOW", "AT",
                    "UNAVAILABLE"
                ),
                "Unknown VWAP position"
            )
            require(
                event.get("trade_signal") is False
                and event.get("research_only") is True,
                "Unsafe event flags"
            )

            key = (
                symbol,
                event["breakout_time_utc"]
            )

            require(key not in seen, "Duplicate event")
            seen.add(key)

            start = datetime.fromisoformat(
                event["breakout_time_utc"]
            )

            require(
                start.tzinfo is not None,
                "Unzoned breakout time"
            )

            end_text = event["confirmation_time_utc"]

            if event["outcome"] == CONFIRMED:
                require(
                    event["pattern"] in (
                        "BULL_TRAP_CONFIRMED",
                        "BEAR_TRAP_CONFIRMED"
                    ) and end_text is not None,
                    "Invalid confirmed-trap metadata"
                )

                end = datetime.fromisoformat(end_text)
                minutes = (
                    end - start
                ).total_seconds() / 60

                require(
                    0 < minutes <= 15,
                    "Invalid confirmation timing"
                )
            else:
                require(
                    end_text is None
                    and event["pattern"] in (
                        "UP_BREAKOUT_ATTEMPT",
                        "DOWN_BREAKOUT_ATTEMPT"
                    ),
                    "Invalid unconfirmed metadata"
                )

            event["symbol"] = symbol
            event["rvol_bucket"] = volume_bucket(
                event["breakout_rvol"]
            )

            all_events.append(event)
            counts[event["outcome"]] += 1

        require(
            len(events)
            == sum(counts.values()),
            "Outcome count mismatch"
        )

        require(
            counts
            == Counter(
                data["symbols"][symbol]["outcomes"]
            ),
            "Stored totals mismatch"
        )

        symbols[symbol] = dict(counts)

    print("\n=== JAHVASCRIPT TRAP PATTERN STUDY ===")
    print("Historical breakout events:", len(all_events))

    total = Counter(
        item["outcome"] for item in all_events
    )

    resolved = total[CONFIRMED] + total[SURVIVED]

    print("Confirmed reversals:", total[CONFIRMED])
    print("No failure within horizon:", total[SURVIVED])
    print("Pending excluded:", total[PENDING])
    print("Resolved sample:", resolved)

    studies = {}

    dimensions = {
        "SYMBOL": ["symbol"],
        "DIRECTION": ["direction"],
        "EMA_ALIGNMENT": ["ema_alignment"],
        "VWAP_POSITION": ["vwap_position"],
        "RELATIVE_VOLUME": ["rvol_bucket"],
        "DIRECTION_EMA_VWAP": [
            "direction", "ema_alignment", "vwap_position"
        ]
    }

    for name, fields in dimensions.items():
        rows = summarize(all_events, fields)
        studies[name] = rows

        print_table(
            name,
            rows,
            minimum=5 if name == "DIRECTION_EMA_VWAP" else 1
        )

    report = {
        "generated_utc":
            datetime.now(timezone.utc).isoformat(),
        "source": "HISTORICAL_TRAP_CONTEXT",
        "breakout_events": len(all_events),
        "resolved_events": resolved,
        "confirmed_reversals": total[CONFIRMED],
        "no_failure_within_horizon": total[SURVIVED],
        "pending_excluded": total[PENDING],
        "volume_buckets": {
            "LOW_RVOL": "RVOL < 0.8",
            "NORMAL_RVOL": "0.8 <= RVOL < 1.2",
            "HIGH_RVOL": "RVOL >= 1.2"
        },
        "study_results": studies,
        "interpretation": (
            "Descriptive historical associations only. "
            "Not predictive, independently price-verified, "
            "or corrected for overlapping breakout events."
        ),
        "historical_research_only": True,
        "source_prices_verified": False,
        "trade_signal": False,
        "execution_enabled": False,
        "trade_authorization": False
    }

    os.umask(0o077)
    OUTPUT.write_text(
        json.dumps(report, indent=2) + "\n"
    )
    OUTPUT.chmod(0o600)

    require(
        len(all_events) == resolved + total[PENDING],
        "Event reconciliation failure"
    )

    print("\nIMPORTANT LIMITATIONS")
    print("Same-session events may overlap")
    print("No independent source-price verification")
    print("No predictive or profitability claim")
    print("No look-ahead features; outcomes are retrospective")
    print("\nSaved: historical_trap_statistics.json")
    print("\nTRAP PATTERN STUDY PASSED")
    print("AI REQUESTS: NONE")
    print("WEBULL REQUESTS: NONE")
    print("ORDER EXECUTION: DISABLED")


if __name__ == "__main__":
    main()
