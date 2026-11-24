"""JahvaScript historical breakout context.
Offline and retrospective. No AI calls or orders.
"""

import json
import os
from collections import Counter
from datetime import datetime, timezone, time
from decimal import Decimal
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parent
JOURNAL = ROOT / "journal"
HISTORY = JOURNAL / "history"
TRAPS = JOURNAL / "historical_traps_latest.json"
INDICATORS = JOURNAL / "technical_indicators_latest.json"
OUTPUT = JOURNAL / "historical_trap_context.json"
ET = ZoneInfo("America/New_York")


def require(ok, message):
    if not ok:
        raise ValueError("STOP: " + message)


def ema_alignment(row):
    values = [
        row.get("ema9"),
        row.get("ema21"),
        row.get("ema55")
    ]

    if any(v is None for v in values):
        return "UNAVAILABLE"

    a, b, c = [Decimal(str(v)) for v in values]

    if a > b > c:
        return "BULLISH_STACK"
    if a < b < c:
        return "BEARISH_STACK"

    return "MIXED"


def vwap_position(close, vwap):
    if vwap is None:
        return "UNAVAILABLE"

    vwap = Decimal(str(vwap))

    if close > vwap:
        return "ABOVE"
    if close < vwap:
        return "BELOW"

    return "AT"


def enrich(symbol, source, trap_data, indicator_data):
    bars = source["bars"]
    series = indicator_data["indicators"][symbol]["series"]

    require(len(bars) == len(series), "Indicator length mismatch")

    indices = {}

    for i, (bar, row) in enumerate(zip(bars, series)):
        dt = datetime.fromisoformat(bar["time"])

        require(
            dt.tzinfo is not None
            and row["time_utc"] == dt.isoformat(),
            "Timestamp alignment mismatch"
        )

        require(
            bar.get("session") == "RTH",
            "Non-RTH historical candle"
        )

        indices[bar["time"]] = i

    enriched = []
    counts = Counter()

    for event in trap_data["symbols"][symbol]["events"]:
        i = indices.get(event["breakout_time_utc"])

        require(i is not None, "Breakout candle not found")
        require(i >= 12, "Insufficient lookback")

        bar = bars[i]
        row = series[i]
        day = event["session_date"]

        require(
            row["session_date"] == day,
            "Breakout session mismatch"
        )

        direction = (
            "UP" if event["kind"] == "BULL_TRAP"
            else "DOWN"
        )

        require(
            event["kind"] in ("BULL_TRAP", "BEAR_TRAP"),
            "Unexpected original event kind"
        )

        outcome = event["status"]

        require(
            outcome in (
                "CONFIRMED_TRAP",
                "NO_FAILURE_WITHIN_HORIZON",
                "AWAITING_MORE_CANDLES"
            ),
            "Unexpected outcome"
        )

        if outcome == "CONFIRMED_TRAP":
            pattern = (
                "BULL_TRAP_CONFIRMED"
                if direction == "UP"
                else "BEAR_TRAP_CONFIRMED"
            )
            require(
                event["confirmation_time_utc"] is not None,
                "Confirmed trap missing confirmation"
            )
        else:
            pattern = (
                "UP_BREAKOUT_ATTEMPT"
                if direction == "UP"
                else "DOWN_BREAKOUT_ATTEMPT"
            )
            require(
                event["confirmation_time_utc"] is None,
                "Unconfirmed breakout has confirmation"
            )

        prior = bars[i-5:i]

        require(
            len(prior) == 5
            and all(
                series[j]["session_date"] == day
                for j in range(i-5, i)
            ),
            "RVOL window crosses session boundary"
        )

        baseline = sum(
            (Decimal(str(b["volume"])) for b in prior),
            Decimal(0)
        ) / Decimal(5)

        volume = Decimal(str(bar["volume"]))
        rvol = volume / baseline if baseline > 0 else None

        day_rows = [
            b for b in bars[:i+1]
            if datetime.fromisoformat(
                b["time"]
            ).astimezone(ET).date().isoformat() == day
        ]

        starts_at_open = (
            bool(day_rows)
            and datetime.fromisoformat(
                day_rows[0]["time"]
            ).astimezone(ET).time() == time(9, 30)
        )

        close = Decimal(str(bar["close"]))
        vwap = (
            row["session_vwap"]
            if starts_at_open else None
        )

        # Input context is taken only from candle i
        # and earlier candles. Outcome is retrospective.
        item = {
            "symbol": symbol,
            "session_date": day,
            "breakout_time_utc": event["breakout_time_utc"],
            "direction": direction,
            "pattern": pattern,
            "outcome": outcome,
            "confirmation_time_utc":
                event["confirmation_time_utc"],
            "breakout_close": str(close),
            "ema_alignment": ema_alignment(row),
            "vwap_position": vwap_position(close, vwap),
            "vwap_session_from_open": starts_at_open,
            "breakout_rvol": (
                round(float(rvol), 3)
                if rvol is not None else None
            ),
            "relative_volume_baseline_bars": 5,
            "future_candles_used_for_outcome_only": True,
            "research_only": True,
            "trade_signal": False
        }

        enriched.append(item)
        counts[outcome] += 1

    require(
        len(enriched)
        == len(trap_data["symbols"][symbol]["events"]),
        "Event count changed"
    )

    return enriched, counts


def main():
    require(
        "journal/" in (
            ROOT / ".gitignore"
        ).read_text().splitlines(),
        "Journal must be gitignored"
    )

    traps = json.loads(TRAPS.read_text())
    indicators = json.loads(INDICATORS.read_text())

    require(
        traps.get("execution_enabled") is False
        and traps.get("trade_authorization") is False,
        "Unsafe trap report"
    )
    require(
        indicators.get("historical_research_only") is True
        and indicators.get("execution_enabled") is False,
        "Unsafe indicator report"
    )

    report = {
        "generated_utc": datetime.now(timezone.utc).isoformat(),
        "source": "WEBULL_SANDBOX_HISTORICAL",
        "historical_research_only": True,
        "outcomes_known_only_after_confirmation": True,
        "live_data_verified": False,
        "execution_enabled": False,
        "trade_authorization": False,
        "symbols": {}
    }

    print("\n=== JAHVASCRIPT TRAP CONTEXT ENGINE ===")

    for symbol in ("SPY", "QQQ"):
        source = json.loads(
            (HISTORY / f"{symbol}_M5.json").read_text()
        )

        require(
            source.get("execution_enabled") is False,
            "Unsafe candle source"
        )

        events, counts = enrich(
            symbol, source, traps, indicators
        )

        report["symbols"][symbol] = {
            "events": events,
            "outcomes": dict(counts)
        }

        print("\n---", symbol, "---")
        print("Events enriched:", len(events))

        print(
            "Confirmed traps:",
            counts["CONFIRMED_TRAP"]
        )
        print(
            "No failure in horizon:",
            counts["NO_FAILURE_WITHIN_HORIZON"]
        )
        print(
            "Pending:",
            counts["AWAITING_MORE_CANDLES"]
        )

        print("\nEMA ALIGNMENT AT BREAKOUT")
        for name, total in sorted(
            Counter(
                e["ema_alignment"] for e in events
            ).items()
        ):
            print(name + ":", total)

        print("\nVWAP POSITION AT BREAKOUT")
        for name, total in sorted(
            Counter(
                e["vwap_position"] for e in events
            ).items()
        ):
            print(name + ":", total)

        require(
            counts["CONFIRMED_TRAP"]
            == traps["symbols"][symbol][
                "outcome_counts"
            ]["CONFIRMED_TRAP"],
            "Confirmed counts differ"
        )

    os.umask(0o077)
    OUTPUT.write_text(
        json.dumps(report, indent=2) + "\n"
    )
    OUTPUT.chmod(0o600)

    print("\nOriginal audited trap report: UNCHANGED")
    print("Saved: historical_trap_context.json")
    print("Pattern labeling: CORRECTED")
    print("Indicator context: HISTORICAL ONLY")
    print("Source price accuracy: NOT VERIFIED")
    print("\nTRAP CONTEXT ENGINE PASSED")
    print("API REQUESTS: NONE")
    print("ORDER EXECUTION: DISABLED")


if __name__ == "__main__":
    main()
