"""Independent V001 breakout and indicator audit.
Offline verification only. No trading or API calls.
"""

import hashlib
import json
from collections import Counter, defaultdict
from datetime import datetime, timedelta, time
from decimal import Decimal
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parent
DATASET = ROOT / "journal/datasets/M5_SPY_QQQ_v001.json"
REPORT = ROOT / "journal/expanded_research_v001.json"
ET = ZoneInfo("America/New_York")

EXPECTED_HASH = (
    "4c55e6c7961e9210f73f1468b20498fe20db4941cd27b10bd11753336292e3cb"
)
STEP = timedelta(minutes=5)
LOOKBACK = 12
HORIZON = 3


def check(ok, message):
    if not ok:
        raise ValueError("AUDIT FAILED: " + message)


def moving_ema(prices, period):
    result = [None] * len(prices)
    previous = sum(prices[:period]) / Decimal(period)
    result[period - 1] = previous

    alpha = Decimal(2) / Decimal(period + 1)

    for i in range(period, len(prices)):
        previous = (
            alpha * prices[i]
            + (Decimal(1) - alpha) * previous
        )
        result[i] = previous

    return result


def prepare(symbol, dataset):
    raw = dataset["symbols"][symbol]["bars"]
    check(len(raw) == 1400, symbol + ": unexpected bars")

    rows = []

    for bar in raw:
        dt = datetime.fromisoformat(bar["time"])
        check(
            dt.tzinfo is not None
            and dt.utcoffset() is not None,
            symbol + ": invalid timezone"
        )

        local = dt.astimezone(ET)

        o, h, l, c, v = [
            Decimal(str(bar[k]))
            for k in ("open", "high", "low", "close", "volume")
        ]

        check(
            all(x.is_finite() for x in (o, h, l, c, v))
            and min(o, h, l, c) > 0
            and l <= min(o, c)
            and h >= max(o, c)
            and v >= 0
            and bar["session"] == "RTH"
            and local.weekday() < 5
            and time(9, 30) <= local.time() < time(16, 0)
            and local.minute % 5 == 0
            and local.second == 0,
            symbol + ": invalid historical candle"
        )

        rows.append({
            "dt": dt,
            "local": local,
            "day": local.date().isoformat(),
            "high": h,
            "low": l,
            "close": c,
            "volume": v
        })

    times = [r["dt"] for r in rows]

    check(
        times == sorted(times)
        and len(times) == len(set(times)),
        symbol + ": timestamp ordering/duplicates"
    )

    closes = [r["close"] for r in rows]

    for period in (9, 21, 55):
        values = moving_ema(closes, period)
        for row, value in zip(rows, values):
            row[f"ema{period}"] = value

    current_day = None
    volume_sum = Decimal(0)
    price_volume_sum = Decimal(0)

    for row in rows:
        if row["day"] != current_day:
            current_day = row["day"]
            volume_sum = Decimal(0)
            price_volume_sum = Decimal(0)

        volume = row["volume"]
        proxy = (
            row["high"] + row["low"] + row["close"]
        ) / Decimal(3)

        volume_sum += volume
        price_volume_sum += proxy * volume

        row["vwap"] = (
            price_volume_sum / volume_sum
            if volume_sum else None
        )

    return rows


def audit_symbol(symbol, dataset, report):
    rows = prepare(symbol, dataset)
    sessions = defaultdict(list)

    for row in rows:
        sessions[row["day"]].append(row)

    full_dates = dataset["full_standard_session_dates"]
    expected_events = []

    for day in full_dates:
        bars = sessions[day]

        check(
            len(bars) == 78
            and bars[0]["local"].time() == time(9, 30)
            and bars[-1]["local"].time() == time(15, 55)
            and all(
                b["dt"] - a["dt"] == STEP
                for a, b in zip(bars, bars[1:])
            ),
            symbol + ": incomplete selected session " + day
        )

        for i in range(LOOKBACK, len(bars)):
            prior = bars[i-LOOKBACK:i]

            ceiling = max(b["high"] for b in prior)
            floor = min(b["low"] for b in prior)

            previous_close = bars[i-1]["close"]
            current = bars[i]

            up = (
                previous_close <= ceiling
                and current["close"] > ceiling
            )

            down = (
                previous_close >= floor
                and current["close"] < floor
            )

            if not (up or down):
                continue

            level = ceiling if up else floor
            future = bars[i+1:i+1+HORIZON]

            confirmation = next(
                (
                    b for b in future
                    if (
                        b["close"] < level if up
                        else b["close"] > level
                    )
                ),
                None
            )

            outcome = (
                "CONFIRMED_REVERSAL"
                if confirmation is not None
                else "PENDING"
                if len(future) < HORIZON
                else "NO_FAILURE_WITHIN_HORIZON"
            )

            a = current["ema9"]
            b = current["ema21"]
            c = current["ema55"]

            alignment = (
                "UNAVAILABLE"
                if any(x is None for x in (a, b, c))
                else "BULLISH_STACK"
                if a > b > c
                else "BEARISH_STACK"
                if a < b < c
                else "MIXED"
            )

            vwap = current["vwap"]

            position = (
                "UNAVAILABLE" if vwap is None
                else "ABOVE" if current["close"] > vwap
                else "BELOW" if current["close"] < vwap
                else "AT"
            )

            volume_baseline = sum(
                (r["volume"] for r in bars[i-5:i]),
                Decimal(0)
            ) / Decimal(5)

            rvol = (
                current["volume"] / volume_baseline
                if volume_baseline > 0 else None
            )

            expected_events.append({
                "date": day,
                "breakout_utc": current["dt"].isoformat(),
                "direction": "UP" if up else "DOWN",
                "outcome": outcome,
                "confirmation_utc": (
                    confirmation["dt"].isoformat()
                    if confirmation else None
                ),
                "ema_alignment": alignment,
                "vwap_position": position,
                "rvol": rvol,
                "cohort": (
                    "EARLIER_BACKFILL_EXPLORATORY"
                    if day < "2026-10-12"
                    else "PREVIOUSLY_EXAMINED_PERIOD"
                )
            })

    actual = report["symbols"][symbol]["events"]

    check(
        len(actual) == len(expected_events),
        symbol + ": event count mismatch"
    )

    verified = 0
    counts = Counter()
    daily = defaultdict(Counter)

    for saved, expected in zip(actual, expected_events):
        for field in (
            "date",
            "breakout_utc",
            "direction",
            "outcome",
            "confirmation_utc",
            "ema_alignment",
            "vwap_position",
            "cohort"
        ):
            check(
                saved[field] == expected[field],
                f"{symbol}: {field} mismatch at event {verified}"
            )

        check(
            saved["symbol"] == symbol
            and saved["trade_signal"] is False
            and saved["prior_lookback_bars"] == LOOKBACK
            and saved["future_confirmation_bars"] == HORIZON,
            symbol + ": invalid event metadata"
        )

        calculated = expected["rvol"]
        reported = saved["rvol"]

        if calculated is None:
            check(reported is None, "Expected missing RVOL")
        else:
            check(
                reported is not None
                and abs(
                    Decimal(str(reported)) - calculated
                ) <= Decimal("0.000051"),
                symbol + ": RVOL mismatch"
            )

        counts[saved["outcome"]] += 1
        daily[saved["date"]][saved["outcome"]] += 1
        verified += 1

    check(
        dict(counts) == report["symbols"][symbol]["outcomes"],
        symbol + ": outcome totals mismatch"
    )

    check(
        {day: dict(values) for day, values in daily.items()}
        == {
            day: values for day, values in
            report["symbols"][symbol]["daily_outcomes"].items()
            if values
        },
        symbol + ": daily outcomes mismatch"
    )

    print(f"\n--- {symbol} ---")
    print("Breakout events independently checked:", verified)
    print("Confirmed reversals:", counts["CONFIRMED_REVERSAL"])
    print(
        "No failure within horizon:",
        counts["NO_FAILURE_WITHIN_HORIZON"]
    )
    print("Pending:", counts["PENDING"])
    print("Breakout levels and direction: PASS")
    print("Confirmation outcomes and timestamps: PASS")
    print("EMA alignment: PASS")
    print("Session VWAP position: PASS")
    print("Relative volume: PASS")
    print("Daily event totals: PASS")

    return counts


def main():
    print("\n=== JAHVASCRIPT EXPANDED ACCURACY AUDIT ===")

    raw = DATASET.read_bytes()
    digest = hashlib.sha256(raw).hexdigest()

    check(digest == EXPECTED_HASH, "Dataset SHA-256 changed")

    dataset = json.loads(raw)
    report = json.loads(REPORT.read_text())

    check(
        dataset.get("execution_enabled") is False
        and dataset.get("trade_authorization") is False
        and dataset.get("historical_research_only") is True,
        "Unsafe dataset"
    )

    check(
        report.get("dataset_sha256") == digest
        and report.get("out_of_sample") is False
        and report.get("execution_enabled") is False
        and report.get("trade_authorization") is False
        and report.get("lookback_bars") == LOOKBACK
        and report.get("confirmation_horizon_bars") == HORIZON,
        "Unexpected research configuration"
    )

    spy = audit_symbol("SPY", dataset, report)
    qqq = audit_symbol("QQQ", dataset, report)

    print("\nTOTAL EVENTS VERIFIED:", sum(spy.values()) + sum(qqq.values()))
    print("Dataset fingerprint: PASS")
    print("Source prices independently verified: NO")
    print("Untouched out-of-sample test: NO")
    print("Predictive trading edge established: NO")

    print("\nEXPANDED ACCURACY AUDIT PASSED")
    print("SOURCE/RESEARCH DATA FILES MODIFIED: NONE")
    print("WEBULL REQUESTS: NONE")
    print("AI REQUESTS: NONE")
    print("ORDER EXECUTION: DISABLED")


if __name__ == "__main__":
    main()
