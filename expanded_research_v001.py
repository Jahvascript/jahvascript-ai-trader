"""JahvaScript V001 expanded historical research.
Offline, retrospective, research-only. No trading.
"""

import hashlib
import json
import os
from collections import Counter
from datetime import datetime, timedelta, time, timezone
from decimal import Decimal
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parent
DATASET = ROOT / "journal/datasets/M5_SPY_QQQ_v001.json"
OUTPUT = ROOT / "journal/expanded_research_v001.json"
ET = ZoneInfo("America/New_York")

EXPECTED_SHA256 = (
    "4c55e6c7961e9210f73f1468b20498fe20db4941cd27b10bd11753336292e3cb"
)
LOOKBACK = 12
HORIZON = 3
STEP = timedelta(minutes=5)


def require(ok, message):
    if not ok:
        raise ValueError("RESEARCH STOPPED: " + message)


def read_dataset():
    raw = DATASET.read_bytes()

    require(
        hashlib.sha256(raw).hexdigest() == EXPECTED_SHA256,
        "V001 dataset fingerprint changed"
    )

    data = json.loads(raw)

    require(
        data.get("dataset_version") == "M5_SPY_QQQ_v001"
        and data.get("historical_research_only") is True
        and data.get("execution_enabled") is False
        and data.get("trade_authorization") is False
        and data.get("untouched_out_of_sample_test") is False,
        "Unsafe or unexpected dataset"
    )

    for entry in data["source_manifest"]:
        source = ROOT / entry["file"]

        require(
            source.resolve().is_relative_to(ROOT.resolve()),
            "Manifest references external path"
        )
        require(
            hashlib.sha256(source.read_bytes()).hexdigest()
            == entry["sha256"],
            "Source fingerprint mismatch"
        )

    require(
        len(data["source_manifest"]) == 8,
        "Unexpected source count"
    )

    return data


def decimal(bar, field):
    value = Decimal(str(bar[field]))
    require(value.is_finite(), "Non-finite candle value")
    return value


def prepare(symbol, data):
    raw = data["symbols"][symbol]["bars"]
    require(len(raw) == 1400, symbol + ": unexpected count")

    rows = []

    for bar in raw:
        dt = datetime.fromisoformat(bar["time"])

        require(
            dt.tzinfo is not None
            and dt.utcoffset() is not None,
            symbol + ": missing timezone"
        )

        local = dt.astimezone(ET)
        o, h, l, c, v = [
            decimal(bar, k)
            for k in ("open", "high", "low", "close", "volume")
        ]

        require(
            bar.get("session") == "RTH"
            and local.weekday() < 5
            and time(9, 30) <= local.time() < time(16, 0)
            and local.minute % 5 == 0
            and local.second == 0
            and min(o, h, l, c) > 0
            and l <= min(o, c)
            and h >= max(o, c)
            and v >= 0,
            symbol + ": invalid candle"
        )

        rows.append({
            "dt": dt,
            "day": local.date().isoformat(),
            "local": local,
            "high": h,
            "low": l,
            "close": c,
            "volume": v
        })

    times = [r["dt"] for r in rows]

    require(
        times == sorted(times)
        and len(times) == len(set(times)),
        symbol + ": unordered or duplicated candles"
    )

    return rows, times


def calculate_ema(rows, period):
    closes = [r["close"] for r in rows]
    result = [None] * len(rows)

    seed = sum(closes[:period], Decimal(0)) / period
    result[period - 1] = seed

    alpha = Decimal(2) / Decimal(period + 1)

    for i in range(period, len(rows)):
        result[i] = (
            result[i-1]
            + alpha * (closes[i] - result[i-1])
        )

    return result


def enrich_indicators(rows):
    emas = {
        n: calculate_ema(rows, n)
        for n in (9, 21, 55)
    }

    active_day = None
    running_pv = Decimal(0)
    running_volume = Decimal(0)

    for i, row in enumerate(rows):
        if row["day"] != active_day:
            active_day = row["day"]
            running_pv = Decimal(0)
            running_volume = Decimal(0)

        typical = (
            row["high"] + row["low"] + row["close"]
        ) / 3

        running_pv += typical * row["volume"]
        running_volume += row["volume"]

        row["vwap"] = (
            running_pv / running_volume
            if running_volume > 0 else None
        )

        row["ema9"] = emas[9][i]
        row["ema21"] = emas[21][i]
        row["ema55"] = emas[55][i]


def analyze_symbol(symbol, rows, full_dates):
    by_day = {}

    for row in rows:
        by_day.setdefault(row["day"], []).append(row)

    events = []
    daily = {}

    for day in sorted(full_dates):
        session = by_day.get(day, [])

        require(
            len(session) == 78
            and session[0]["local"].time() == time(9, 30)
            and session[-1]["local"].time() == time(15, 55),
            symbol + ": incomplete expected session"
        )

        require(
            all(
                b["dt"] - a["dt"] == STEP
                for a, b in zip(session, session[1:])
            ),
            symbol + ": intraday candle gap"
        )

        outcomes = Counter()

        for i in range(LOOKBACK, len(session)):
            previous = session[i-LOOKBACK:i]

            resistance = max(b["high"] for b in previous)
            support = min(b["low"] for b in previous)

            prior_close = session[i-1]["close"]
            current = session[i]
            close = current["close"]

            bull = prior_close <= resistance < close
            bear = prior_close >= support > close

            if not (bull or bear):
                continue

            direction = "UP" if bull else "DOWN"
            level = resistance if bull else support

            future = session[i+1:i+1+HORIZON]
            confirmation = None

            for next_bar in future:
                failed = (
                    next_bar["close"] < level if bull
                    else next_bar["close"] > level
                )

                if failed:
                    confirmation = next_bar
                    break

            if confirmation is not None:
                outcome = "CONFIRMED_REVERSAL"
            elif len(future) < HORIZON:
                outcome = "PENDING"
            else:
                outcome = "NO_FAILURE_WITHIN_HORIZON"

            a, b, c = (
                current["ema9"],
                current["ema21"],
                current["ema55"]
            )

            if any(v is None for v in (a, b, c)):
                ema_state = "UNAVAILABLE"
            elif a > b > c:
                ema_state = "BULLISH_STACK"
            elif a < b < c:
                ema_state = "BEARISH_STACK"
            else:
                ema_state = "MIXED"

            vwap = current["vwap"]

            vwap_state = (
                "UNAVAILABLE" if vwap is None
                else "ABOVE" if close > vwap
                else "BELOW" if close < vwap
                else "AT"
            )

            baseline = (
                sum(
                    (
                        bar["volume"]
                        for bar in session[i-5:i]
                    ),
                    Decimal(0)
                ) / 5
            )

            rvol = (
                current["volume"] / baseline
                if baseline > 0 else None
            )

            cohort = (
                "EARLIER_BACKFILL_EXPLORATORY"
                if day < "2026-10-12"
                else "PREVIOUSLY_EXAMINED_PERIOD"
            )

            events.append({
                "symbol": symbol,
                "date": day,
                "cohort": cohort,
                "breakout_utc": current["dt"].isoformat(),
                "direction": direction,
                "outcome": outcome,
                "confirmation_utc": (
                    confirmation["dt"].isoformat()
                    if confirmation else None
                ),
                "ema_alignment": ema_state,
                "vwap_position": vwap_state,
                "rvol": (
                    round(float(rvol), 4)
                    if rvol is not None else None
                ),
                "prior_lookback_bars": LOOKBACK,
                "future_confirmation_bars": HORIZON,
                "trade_signal": False
            })

            outcomes[outcome] += 1

        daily[day] = dict(outcomes)

    return events, daily


def main():
    print("\n=== JAHVASCRIPT EXPANDED RESEARCH V001 ===")

    require(
        "journal/" in (ROOT / ".gitignore").read_text().splitlines(),
        "Journal must be gitignored"
    )

    require(
        not OUTPUT.exists(),
        "Expanded V001 report exists; refusing overwrite"
    )

    data = read_dataset()
    full_dates = data["full_standard_session_dates"]

    require(
        len(full_dates) == 17
        and set(data["partial_session_dates"])
        == {"2026-09-25", "2026-10-21"},
        "Unexpected session coverage"
    )

    prepared = {}
    timelines = {}

    for symbol in ("SPY", "QQQ"):
        rows, times = prepare(symbol, data)
        enrich_indicators(rows)
        prepared[symbol] = rows
        timelines[symbol] = times

    require(
        timelines["SPY"] == timelines["QQQ"],
        "SPY/QQQ timestamp mismatch"
    )

    report = {
        "dataset": "M5_SPY_QQQ_v001",
        "dataset_sha256": EXPECTED_SHA256,
        "generated_utc": datetime.now(
            timezone.utc
        ).isoformat(),
        "lookback_bars": LOOKBACK,
        "confirmation_horizon_bars": HORIZON,
        "partial_sessions_excluded": data[
            "partial_session_dates"
        ],
        "out_of_sample": False,
        "historical_research_only": True,
        "source_prices_verified": False,
        "trade_signal": False,
        "execution_enabled": False,
        "trade_authorization": False,
        "symbols": {}
    }

    for symbol in ("SPY", "QQQ"):
        events, daily = analyze_symbol(
            symbol, prepared[symbol], full_dates
        )

        counts = Counter(e["outcome"] for e in events)
        cohorts = Counter(e["cohort"] for e in events)

        report["symbols"][symbol] = {
            "events": events,
            "daily_outcomes": daily,
            "outcomes": dict(counts)
        }

        print(f"\n--- {symbol} ---")
        print("Source candles:", len(prepared[symbol]))
        print("Complete sessions analyzed:", len(full_dates))
        print("Breakout attempts:", len(events))
        print(
            "Confirmed reversals:",
            counts["CONFIRMED_REVERSAL"]
        )
        print(
            "No failure within 15 minutes:",
            counts["NO_FAILURE_WITHIN_HORIZON"]
        )
        print("Pending:", counts["PENDING"])
        print(
            "Earlier backfill events:",
            cohorts["EARLIER_BACKFILL_EXPLORATORY"]
        )
        print(
            "Previously examined period events:",
            cohorts["PREVIOUSLY_EXAMINED_PERIOD"]
        )

    os.umask(0o077)

    with OUTPUT.open("x") as handle:
        json.dump(report, handle, indent=2)
        handle.write("\n")

    OUTPUT.chmod(0o600)

    print("\nSPY/QQQ source alignment: PASS")
    print("Original research: UNCHANGED")
    print("Partial sessions: EXCLUDED")
    print("Future bars used for outcomes only: YES")
    print("Untouched out-of-sample test: NO")
    print("Source prices independently verified: NO")
    print("Saved: journal/expanded_research_v001.json")

    print("\nEXPANDED RESEARCH V001 PASSED")
    print("WEBULL REQUESTS: NONE")
    print("AI REQUESTS: NONE")
    print("ORDER EXECUTION: DISABLED")


if __name__ == "__main__":
    main()
