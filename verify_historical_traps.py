"""Independent historical breakout audit.
Offline research only. No API calls or orders.
"""

import json
from collections import Counter
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parent
HISTORY = ROOT / "journal/history"
REPORT = ROOT / "journal/historical_traps_latest.json"
ET = ZoneInfo("America/New_York")

LOOKBACK = 12
HORIZON = 3


def require(condition, message):
    if not condition:
        raise ValueError("AUDIT FAILED: " + message)


def audit(symbol, report):
    source = json.loads(
        (HISTORY / f"{symbol}_M5.json").read_text()
    )

    bars = source["bars"]
    stored = report["symbols"][symbol]["events"]

    require(
        source.get("execution_enabled") is False,
        "Unsafe historical source"
    )

    sessions = {}

    for bar in bars:
        dt = datetime.fromisoformat(bar["time"])
        require(dt.tzinfo is not None, "Missing timezone")

        day = dt.astimezone(ET).date().isoformat()
        sessions.setdefault(day, []).append(bar)

    saved_events = {}

    for event in stored:
        key = (
            event["session_date"],
            event["breakout_time_utc"]
        )
        require(key not in saved_events, "Duplicate event")
        saved_events[key] = event

    counts = Counter()
    checked = 0

    for day, candles in sessions.items():
        for i in range(LOOKBACK, len(candles)):
            previous = candles[i - LOOKBACK:i]

            support = min(
                float(b["low"]) for b in previous
            )
            resistance = max(
                float(b["high"]) for b in previous
            )

            prior_close = float(candles[i-1]["close"])
            current_close = float(candles[i]["close"])

            bull = prior_close <= resistance < current_close
            bear = prior_close >= support > current_close

            key = (day, candles[i]["time"])
            event = saved_events.get(key)

            if not bull and not bear:
                require(
                    event is None,
                    "Unexpected saved breakout"
                )
                continue

            require(event is not None, "Missing breakout")

            kind = "BULL_TRAP" if bull else "BEAR_TRAP"
            require(event["kind"] == kind, "Wrong direction")

            require(
                abs(event["support"] - support) < 0.0001,
                "Support mismatch"
            )
            require(
                abs(event["resistance"] - resistance) < 0.0001,
                "Resistance mismatch"
            )

            future = candles[i+1:i+1+HORIZON]
            level = resistance if bull else support

            failure_index = None

            for j, bar in enumerate(future, i+1):
                close = float(bar["close"])

                failed = (
                    close < level if bull
                    else close > level
                )

                if failed:
                    failure_index = j
                    break

            if failure_index is not None:
                expected = "CONFIRMED_TRAP"
                confirmation = candles[
                    failure_index
                ]["time"]
            elif len(future) < HORIZON:
                expected = "AWAITING_MORE_CANDLES"
                confirmation = None
            else:
                expected = "NO_FAILURE_WITHIN_HORIZON"
                confirmation = None

            require(
                event["status"] == expected,
                "Incorrect breakout outcome"
            )

            require(
                event["confirmation_time_utc"]
                == confirmation,
                "Incorrect confirmation time"
            )

            if confirmation is not None:
                require(
                    failure_index > i,
                    "Look-ahead timestamp failure"
                )
                require(
                    failure_index - i <= HORIZON,
                    "Confirmation outside horizon"
                )

            counts[expected] += 1
            checked += 1

    require(
        checked == len(saved_events),
        "Event count mismatch"
    )

    require(
        dict(counts)
        == {
            k: v
            for k, v in report["symbols"][symbol][
                "outcome_counts"
            ].items() if v
        },
        "Outcome totals mismatch"
    )

    print(f"\n--- {symbol} ---")
    print("Breakout events independently checked:", checked)
    print("Confirmed reversals:", counts["CONFIRMED_TRAP"])
    print(
        "No failure within horizon:",
        counts["NO_FAILURE_WITHIN_HORIZON"]
    )
    print(
        "Pending events:",
        counts["AWAITING_MORE_CANDLES"]
    )
    print("Historical price-level verification: PASS")
    print("Confirmation timestamp verification: PASS")

    return counts


def main():
    report = json.loads(REPORT.read_text())

    require(
        report.get("execution_enabled") is False
        and report.get("trade_authorization") is False
        and report.get("research_only") is True,
        "Unsafe report"
    )

    require(
        report.get("lookback_bars") == LOOKBACK
        and report.get("confirmation_horizon_bars")
        == HORIZON,
        "Unexpected detector configuration"
    )

    print("\n=== JAHVASCRIPT TRAP ACCURACY AUDIT ===")

    spy = audit("SPY", report)
    qqq = audit("QQQ", report)

    total = sum(spy.values()) + sum(qqq.values())

    print("\nTotal events independently verified:", total)
    print("Forward-looking trade signals: NONE")
    print("Source prices: NOT INDEPENDENTLY VERIFIED")
    print("Market data: HISTORICAL")
    print("\nTRAP ACCURACY AUDIT PASSED")
    print("FILES MODIFIED: NONE")
    print("API REQUESTS: NONE")
    print("ORDER EXECUTION: DISABLED")


if __name__ == "__main__":
    main()
