"""JahvaScript historical market-structure research.
Offline only. Retrospective events, never trade signals.
"""

import json
import math
import os
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

from market_trap_detector import classify

ROOT = Path(__file__).resolve().parent
HISTORY = ROOT / "journal/history"
INDICATORS = ROOT / "journal/technical_indicators_latest.json"
OUTPUT = ROOT / "journal/historical_traps_latest.json"

LOOKBACK = 12
HORIZON = 3


def detect_session(bars):
    events = []

    for i in range(LOOKBACK, len(bars)):
        previous = bars[i - LOOKBACK:i]

        resistance = max(float(b["high"]) for b in previous)
        support = min(float(b["low"]) for b in previous)

        prior_close = float(bars[i - 1]["close"])
        current_close = float(bars[i]["close"])

        bull = prior_close <= resistance < current_close
        bear = prior_close >= support > current_close

        if not (bull or bear):
            continue

        # Five earlier bars provide the detector's RVOL baseline.
        start = i - 5
        end = min(len(bars), i + HORIZON + 1)
        window = bars[start:end]

        result = classify(
            window,
            support,
            resistance,
            horizon=HORIZON
        )

        relative_index = i - start
        matched = [
            e for e in (
                result["confirmed"] + result["pending"]
            )
            if e["breakout_bar"] == relative_index
        ]

        if result["confirmed"] and any(
            e["breakout_bar"] == relative_index
            for e in result["confirmed"]
        ):
            event = next(
                e for e in result["confirmed"]
                if e["breakout_bar"] == relative_index
            )
            status = "CONFIRMED_TRAP"
            confirmation_index = (
                start + event["confirmation_bar"]
            )

        elif result["pending"] and any(
            e["breakout_bar"] == relative_index
            for e in result["pending"]
        ):
            event = matched[0]
            status = "AWAITING_MORE_CANDLES"
            confirmation_index = None

        else:
            # The full confirmation window elapsed
            # without a qualifying failure.
            status = "NO_FAILURE_WITHIN_HORIZON"
            confirmation_index = None
            event = {
                "breakout_relative_volume": None
            }

        events.append({
            "kind": "BULL_TRAP" if bull else "BEAR_TRAP",
            "status": status,
            "support": round(support, 4),
            "resistance": round(resistance, 4),
            "breakout_time_utc": bars[i]["time"],
            "confirmation_time_utc": (
                bars[confirmation_index]["time"]
                if confirmation_index is not None
                else None
            ),
            "breakout_relative_volume":
                event["breakout_relative_volume"],
            "lookback_bars": LOOKBACK,
            "confirmation_horizon_bars": HORIZON,
            "retrospective_research": True,
            "trade_signal": False
        })

    return events


def load_symbol(symbol, indicators):
    path = HISTORY / f"{symbol}_M5.json"
    data = json.loads(path.read_text())

    if (
        data.get("symbol") != symbol
        or data.get("timeframe") != "M5"
        or data.get("execution_enabled") is not False
    ):
        raise ValueError(symbol + ": unexpected source")

    bars = data["bars"]
    series = indicators["indicators"][symbol]["series"]

    if len(bars) != len(series):
        raise ValueError(symbol + ": indicator length mismatch")

    sessions = {}
    previous_time = None

    for bar, indicator in zip(bars, series):
        dt = datetime.fromisoformat(bar["time"])

        if dt.tzinfo is None or bar.get("session") != "RTH":
            raise ValueError(symbol + ": invalid timestamp/session")

        if previous_time is not None and dt <= previous_time:
            raise ValueError(symbol + ": bars unordered")
        previous_time = dt

        if indicator["time_utc"] != dt.isoformat():
            raise ValueError(symbol + ": indicator alignment failure")

        if not all(
            math.isfinite(float(bar[k]))
            for k in ("open", "high", "low", "close", "volume")
        ):
            raise ValueError(symbol + ": invalid numeric candle")

        day = indicator["session_date"]
        sessions.setdefault(day, []).append(bar)

    return sessions


def self_test():
    def bar(close, high, low):
        return {
            "time": "TEST",
            "close": close,
            "high": high,
            "low": low,
            "volume": 1000
        }

    base = [bar(100, 101, 99) for _ in range(12)]

    bullish_failure = base + [
        bar(102, 103, 101),
        bar(100, 101, 99)
    ]

    bearish_failure = base + [
        bar(98, 99, 97),
        bar(100, 101, 99)
    ]

    for test_bars, expected in (
        (bullish_failure, "BULL_TRAP"),
        (bearish_failure, "BEAR_TRAP")
    ):
        events = detect_session(test_bars)

        assert len(events) == 1
        assert events[0]["kind"] == expected
        assert events[0]["status"] == "CONFIRMED_TRAP"
        assert events[0]["confirmation_time_utc"] == "TEST"

    print("Synthetic bull/bear trap tests: PASS")


def main():
    if "journal/" not in (
        ROOT / ".gitignore"
    ).read_text().splitlines():
        raise SystemExit("STOP: Journal not gitignored")

    indicators = json.loads(INDICATORS.read_text())

    if (
        indicators.get("historical_research_only") is not True
        or indicators.get("live_prices_verified") is not False
        or indicators.get("execution_enabled") is not False
    ):
        raise SystemExit("STOP: Unsafe indicator input")

    self_test()

    report = {
        "generated_utc": datetime.now(
            timezone.utc
        ).isoformat(),
        "source": "WEBULL_SANDBOX_HISTORICAL",
        "lookback_bars": LOOKBACK,
        "confirmation_horizon_bars": HORIZON,
        "source_prices_independently_verified": False,
        "research_only": True,
        "execution_enabled": False,
        "trade_authorization": False,
        "symbols": {}
    }

    print("\n=== JAHVASCRIPT HISTORICAL TRAP RESEARCH ===")

    for symbol in ("SPY", "QQQ"):
        sessions = load_symbol(symbol, indicators)
        all_events = []

        for day, bars in sorted(sessions.items()):
            events = detect_session(bars)

            for event in events:
                event["session_date"] = day

            all_events.extend(events)

        counts = Counter(
            event["status"] for event in all_events
        )

        report["symbols"][symbol] = {
            "sessions_analyzed": len(sessions),
            "events": all_events,
            "outcome_counts": dict(counts)
        }

        print("\n---", symbol, "---")
        print("Sessions:", len(sessions))
        print("Breakout attempts:", len(all_events))
        print("Confirmed traps:", counts["CONFIRMED_TRAP"])
        print(
            "No failure within horizon:",
            counts["NO_FAILURE_WITHIN_HORIZON"]
        )
        print(
            "Awaiting additional candles:",
            counts["AWAITING_MORE_CANDLES"]
        )

        print("LATEST 5 EVENTS")
        for event in all_events[-5:]:
            print(
                event["session_date"],
                event["kind"],
                "|", event["status"],
                "| RVOL:",
                event["breakout_relative_volume"]
            )

    os.umask(0o077)
    OUTPUT.parent.mkdir(exist_ok=True)
    OUTPUT.write_text(
        json.dumps(report, indent=2) + "\n"
    )
    OUTPUT.chmod(0o600)

    print("\nSaved: historical_traps_latest.json")
    print("Analysis: HISTORICAL / RETROSPECTIVE")
    print("Future candles used only to confirm past events")
    print("Current trade signals: NONE")
    print("\nHISTORICAL TRAP ENGINE PASSED")
    print("API REQUESTS: NONE")
    print("ORDER EXECUTION: DISABLED")


if __name__ == "__main__":
    main()
