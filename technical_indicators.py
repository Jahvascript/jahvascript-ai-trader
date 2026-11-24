"""JahvaScript historical technical indicators.
Offline research only. No APIs or trading.
"""

import json
import os
from collections import defaultdict
from datetime import datetime, timezone, time
from decimal import Decimal
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parent
HISTORY = ROOT / "journal/history"
OUTPUT = ROOT / "journal/technical_indicators_latest.json"
EASTERN = ZoneInfo("America/New_York")

PERIODS = (9, 21, 55)


def load_bars(symbol):
    path = HISTORY / f"{symbol}_M5.json"
    data = json.loads(path.read_text())

    if (
        data.get("symbol") != symbol
        or data.get("timeframe") != "M5"
        or data.get("execution_enabled") is not False
    ):
        raise ValueError("Unexpected historical data configuration")

    rows = []

    for bar in data["bars"]:
        dt = datetime.fromisoformat(bar["time"])

        if dt.tzinfo is None or dt.utcoffset() is None:
            raise ValueError("Timestamp has no timezone")

        eastern = dt.astimezone(EASTERN)

        if not (
            eastern.weekday() < 5
            and time(9, 30) <= eastern.time() < time(16, 0)
            and eastern.minute % 5 == 0
            and eastern.second == 0
            and eastern.microsecond == 0
            and bar.get("session") == "RTH"
        ):
            raise ValueError("Invalid regular-hours candle")

        values = {
            key: Decimal(str(bar[key]))
            for key in ("open", "high", "low", "close", "volume")
        }

        o, h, l, c, v = (
            values[k]
            for k in ("open", "high", "low", "close", "volume")
        )

        if (
            not all(x.is_finite() for x in values.values())
            or min(o, h, l, c) <= 0
            or v < 0
            or h < max(o, c)
            or l > min(o, c)
            or h < l
        ):
            raise ValueError("Invalid OHLCV candle")

        rows.append({
            "dt": dt,
            "eastern": eastern,
            "values": values
        })

    times = [row["dt"] for row in rows]

    if (
        len(rows) < max(PERIODS)
        or len(times) != len(set(times))
        or times != sorted(times)
    ):
        raise ValueError("Missing, duplicate or unordered candles")

    return rows


def ema_series(closes, period):
    """SMA-seeded EMA; unavailable before period bars."""
    result = [None] * len(closes)

    seed = sum(closes[:period]) / Decimal(period)
    result[period - 1] = seed

    alpha = Decimal(2) / Decimal(period + 1)
    previous = seed

    for i in range(period, len(closes)):
        previous = (
            closes[i] * alpha
            + previous * (Decimal(1) - alpha)
        )
        result[i] = previous

    return result


def calculate(symbol, rows):
    closes = [
        row["values"]["close"]
        for row in rows
    ]

    emas = {
        period: ema_series(closes, period)
        for period in PERIODS
    }

    cumulative_pv = Decimal(0)
    cumulative_volume = Decimal(0)
    active_day = None
    session_counts = defaultdict(int)
    results = []

    for i, row in enumerate(rows):
        eastern = row["eastern"]
        day = eastern.date().isoformat()
        values = row["values"]

        if day != active_day:
            active_day = day
            cumulative_pv = Decimal(0)
            cumulative_volume = Decimal(0)

        session_counts[day] += 1

        typical = (
            values["high"]
            + values["low"]
            + values["close"]
        ) / Decimal(3)

        volume = values["volume"]

        cumulative_pv += typical * volume
        cumulative_volume += volume

        vwap = (
            cumulative_pv / cumulative_volume
            if cumulative_volume > 0
            else None
        )

        results.append({
            "time_utc": row["dt"].isoformat(),
            "time_eastern": eastern.isoformat(),
            "session_date": day,
            "close": str(values["close"]),
            "volume": str(volume),
            "ema9": (
                str(emas[9][i])
                if emas[9][i] is not None else None
            ),
            "ema21": (
                str(emas[21][i])
                if emas[21][i] is not None else None
            ),
            "ema55": (
                str(emas[55][i])
                if emas[55][i] is not None else None
            ),
            "session_vwap": (
                str(vwap) if vwap is not None else None
            )
        })

    return results, dict(session_counts)


def display(value):
    if value is None:
        return "UNAVAILABLE"
    return f"{Decimal(value):.4f}"


def main():
    ignored = (ROOT / ".gitignore").read_text().splitlines()

    if "journal/" not in ignored:
        raise SystemExit("STOP: Journal must be gitignored")

    report = {
        "generated_utc": datetime.now(timezone.utc).isoformat(),
        "source": "WEBULL_SANDBOX_HISTORICAL",
        "indicators": {},
        "historical_research_only": True,
        "live_prices_verified": False,
        "execution_enabled": False,
        "trade_authorization": False
    }

    times_by_symbol = {}

    print("\n=== JAHVASCRIPT TECHNICAL INDICATORS ===")

    for symbol in ("SPY", "QQQ"):
        rows = load_bars(symbol)
        indicators, sessions = calculate(symbol, rows)

        times_by_symbol[symbol] = [
            row["dt"] for row in rows
        ]

        assert indicators[0]["ema9"] is None
        assert indicators[8]["ema9"] is not None
        assert indicators[20]["ema21"] is not None
        assert indicators[54]["ema55"] is not None

        last = indicators[-1]

        report["indicators"][symbol] = {
            "bars_analyzed": len(rows),
            "session_bar_counts": sessions,
            "latest_candle_utc": last["time_utc"],
            "latest_candle_eastern": last["time_eastern"],
            "series": indicators
        }

        print("\n---", symbol, "---")
        print("Candles analyzed:", len(rows))
        print("Trading sessions represented:", len(sessions))
        print("Latest historical candle:", last["time_eastern"])
        print("Close:", display(last["close"]))
        print("EMA 9:", display(last["ema9"]))
        print("EMA 21:", display(last["ema21"]))
        print("EMA 55:", display(last["ema55"]))
        print("Session VWAP:", display(last["session_vwap"]))

        latest_day = last["session_date"]

        print(
            "Latest session candles:",
            sessions[latest_day], "of 78 possible"
        )

        print(
            "Latest session complete:",
            sessions[latest_day] == 78
        )

    if times_by_symbol["SPY"] != times_by_symbol["QQQ"]:
        raise SystemExit("STOP: SPY/QQQ timestamps differ")

    os.umask(0o077)
    OUTPUT.parent.mkdir(exist_ok=True)
    OUTPUT.write_text(
        json.dumps(report, indent=2) + "\n"
    )
    OUTPUT.chmod(0o600)

    print("\nSPY/QQQ candle alignment: PASS")
    print("Chronological EMA calculations: PASS")
    print("Daily-reset session VWAP: PASS")
    print("Saved: technical_indicators_latest.json")
    print("Market data: HISTORICAL / NOT LIVE VERIFIED")
    print("\nTECHNICAL INDICATOR ENGINE PASSED")
    print("API REQUESTS: NONE")
    print("ORDER EXECUTION: DISABLED")


if __name__ == "__main__":
    main()
