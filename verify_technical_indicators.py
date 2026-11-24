"""Independent offline audit of JahvaScript indicators.
No provider APIs, AI requests, or trading functionality.
"""

import json
from collections import defaultdict
from datetime import datetime, time
from decimal import Decimal
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parent
HISTORY = ROOT / "journal/history"
OUTPUT = ROOT / "journal/technical_indicators_latest.json"
ET = ZoneInfo("America/New_York")
TOLERANCE = Decimal("0.000001")


def require(condition, message):
    if not condition:
        raise ValueError("AUDIT FAILED: " + message)


def compare(actual, expected, label):
    if expected is None:
        require(actual is None, label + ": expected NULL")
        return Decimal(0)

    require(actual is not None, label + ": missing value")
    difference = abs(Decimal(str(actual)) - expected)
    require(difference <= TOLERANCE, label + ": mismatch")
    return difference


def independent_ema(closes, period):
    result = [None] * len(closes)

    seed = sum(closes[:period], Decimal(0)) / period
    result[period - 1] = seed

    alpha = Decimal(2) / Decimal(period + 1)

    for i in range(period, len(closes)):
        previous = result[i - 1]
        result[i] = previous + alpha * (
            closes[i] - previous
        )

    return result


def audit(symbol, report):
    source = json.loads(
        (HISTORY / f"{symbol}_M5.json").read_text()
    )
    rows = source["bars"]
    saved = report["indicators"][symbol]["series"]

    require(
        source.get("execution_enabled") is False,
        symbol + ": unsafe source"
    )
    require(
        len(rows) == len(saved) and len(rows) >= 55,
        symbol + ": incorrect series length"
    )

    closes = [Decimal(str(b["close"])) for b in rows]
    checks = {
        period: independent_ema(closes, period)
        for period in (9, 21, 55)
    }

    sessions = defaultdict(list)
    timestamps = []
    max_error = Decimal(0)
    comparisons = 0

    for index, (bar, indicator) in enumerate(
        zip(rows, saved)
    ):
        dt = datetime.fromisoformat(bar["time"])
        eastern = dt.astimezone(ET)

        require(
            dt.tzinfo is not None,
            symbol + ": missing timezone"
        )
        require(
            indicator["time_utc"] == dt.isoformat(),
            symbol + ": UTC alignment failure"
        )
        require(
            indicator["time_eastern"] == eastern.isoformat(),
            symbol + ": Eastern alignment failure"
        )
        require(
            indicator["session_date"]
            == eastern.date().isoformat(),
            symbol + ": session-date mismatch"
        )
        require(
            bar["session"] == "RTH",
            symbol + ": unexpected session"
        )
        require(
            eastern.weekday() < 5
            and time(9, 30) <= eastern.time() < time(16, 0),
            symbol + ": outside regular hours"
        )

        timestamps.append(dt)
        sessions[eastern.date().isoformat()].append(index)

        for period in (9, 21, 55):
            error = compare(
                indicator[f"ema{period}"],
                checks[period][index],
                f"{symbol} EMA{period} row {index}"
            )
            max_error = max(max_error, error)
            comparisons += 1

        compare(
            indicator["close"],
            Decimal(str(bar["close"])),
            f"{symbol} close row {index}"
        )
        compare(
            indicator["volume"],
            Decimal(str(bar["volume"])),
            f"{symbol} volume row {index}"
        )

    require(
        len(timestamps) == len(set(timestamps))
        and timestamps == sorted(timestamps),
        symbol + ": timestamp ordering/duplicates"
    )

    # Independently recompute VWAP within each date.
    # OHLC3 is the same documented candle-price proxy.
    partial_starts = []

    for day, indices in sorted(sessions.items()):
        volume_sum = Decimal(0)
        price_volume_sum = Decimal(0)

        first_time = datetime.fromisoformat(
            rows[indices[0]]["time"]
        ).astimezone(ET).time()

        if first_time != time(9, 30):
            partial_starts.append(day)

        for position in indices:
            bar = rows[position]

            high = Decimal(str(bar["high"]))
            low = Decimal(str(bar["low"]))
            close = Decimal(str(bar["close"]))
            volume = Decimal(str(bar["volume"]))

            proxy_price = (high + low + close) / 3

            volume_sum += volume
            price_volume_sum += proxy_price * volume

            expected_vwap = (
                price_volume_sum / volume_sum
                if volume_sum > 0 else None
            )

            error = compare(
                saved[position]["session_vwap"],
                expected_vwap,
                f"{symbol} VWAP row {position}"
            )
            max_error = max(max_error, error)
            comparisons += 1

    expected_counts = {
        day: len(indices)
        for day, indices in sessions.items()
    }

    require(
        report["indicators"][symbol]["session_bar_counts"]
        == expected_counts,
        symbol + ": session counts mismatch"
    )

    print(f"\n--- {symbol} ---")
    print("Candles checked:", len(rows))
    print("EMA periods independently checked: 9, 21, 55")
    print("VWAP resets checked:", len(sessions))
    print("Total numerical comparisons:", comparisons)
    print("Maximum numerical difference:", max_error)
    print("All comparisons within tolerance: PASS")
    print(
        "Sessions missing opening candles:",
        ", ".join(partial_starts) or "none"
    )

    return timestamps


def main():
    require(
        "journal/" in (
            ROOT / ".gitignore"
        ).read_text().splitlines(),
        "Journal not gitignored"
    )

    report = json.loads(OUTPUT.read_text())

    require(
        report.get("historical_research_only") is True
        and report.get("live_prices_verified") is False
        and report.get("execution_enabled") is False
        and report.get("trade_authorization") is False,
        "Unsafe indicator report"
    )

    print("\n=== JAHVASCRIPT INDICATOR ACCURACY AUDIT ===")

    spy_times = audit("SPY", report)
    qqq_times = audit("QQQ", report)

    require(
        spy_times == qqq_times,
        "SPY and QQQ timestamps differ"
    )

    print("\nSPY/QQQ alignment: PASS")
    print("Independent EMA verification: PASS")
    print("Independent VWAP verification: PASS")
    print("Session boundary verification: PASS")
    print("Source price accuracy: NOT VERIFIED")
    print("Live quote freshness: NOT VERIFIED")
    print("Exchange-calendar coverage: NOT VERIFIED")
    print("\nINDICATOR ACCURACY AUDIT PASSED")
    print("FILES MODIFIED: NONE (source/report data)")
    print("API REQUESTS: NONE")
    print("ORDER EXECUTION: DISABLED")


if __name__ == "__main__":
    main()
