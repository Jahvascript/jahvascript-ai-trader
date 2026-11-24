"""JahvaScript historical market-state integration.
Offline research only. No market APIs, AI, or orders.
"""

import hashlib
import json
import os
from collections import Counter, defaultdict
from datetime import datetime, timezone, timedelta
from decimal import Decimal
from pathlib import Path
from zoneinfo import ZoneInfo

from rvol_features import calculate_dual_rvol

ROOT = Path(__file__).resolve().parent
JOURNAL = ROOT / "journal"
DATASET = JOURNAL / "datasets/M5_SPY_QQQ_v001.json"
RESEARCH = JOURNAL / "expanded_research_v001.json"
CLOCK = JOURNAL / "rvol_clock_normalized_v001.json"
OUTPUT = JOURNAL / "market_state_v001.json"

ET = ZoneInfo("America/New_York")
EXPECTED_DATASET_HASH = (
    "4c55e6c7961e9210f73f1468b20498fe20db4941cd27b10bd11753336292e3cb"
)

LOOKBACK = 12
HORIZON = 3
REFERENCE_DAYS = 5


def require(ok, message):
    if not ok:
        raise ValueError("INTEGRATION STOPPED: " + message)


def digest(data):
    return hashlib.sha256(data).hexdigest()


def ema(prices, period):
    values = [None] * len(prices)
    previous = sum(prices[:period], Decimal(0)) / period
    values[period - 1] = previous
    alpha = Decimal(2) / Decimal(period + 1)

    for i in range(period, len(prices)):
        previous += alpha * (prices[i] - previous)
        values[i] = previous

    return values


def prepare(symbol, dataset):
    bars = dataset["symbols"][symbol]["bars"]
    require(len(bars) == 1400, "Unexpected candle count")

    rows = []

    for bar in bars:
        dt = datetime.fromisoformat(bar["time"])
        require(
            dt.tzinfo is not None and dt.utcoffset() is not None,
            "Unzoned candle"
        )

        local = dt.astimezone(ET)
        prices = {
            key: Decimal(str(bar[key]))
            for key in ("open", "high", "low", "close", "volume")
        }

        o, h, l, c, v = (
            prices[k]
            for k in ("open", "high", "low", "close", "volume")
        )

        require(
            all(x.is_finite() for x in prices.values())
            and min(o, h, l, c) > 0
            and l <= min(o, c)
            and h >= max(o, c)
            and v >= 0
            and bar.get("session") == "RTH",
            "Invalid historical candle"
        )

        rows.append({
            "time": dt.astimezone(timezone.utc),
            "day": local.date().isoformat(),
            "slot": local.strftime("%H:%M"),
            **prices
        })

    times = [r["time"] for r in rows]

    require(
        times == sorted(times)
        and len(times) == len(set(times)),
        "Unordered or duplicated candles"
    )

    closes = [r["close"] for r in rows]

    for period in (9, 21, 55):
        calculated = ema(closes, period)
        for row, value in zip(rows, calculated):
            row[f"ema{period}"] = value

    active_day = None
    cumulative_volume = Decimal(0)
    cumulative_pv = Decimal(0)

    for row in rows:
        if row["day"] != active_day:
            active_day = row["day"]
            cumulative_volume = Decimal(0)
            cumulative_pv = Decimal(0)

        typical = (
            row["high"] + row["low"] + row["close"]
        ) / 3

        cumulative_pv += typical * row["volume"]
        cumulative_volume += row["volume"]

        row["vwap"] = (
            cumulative_pv / cumulative_volume
            if cumulative_volume > 0
            else None
        )

    sessions = defaultdict(list)

    for row in rows:
        sessions[row["day"]].append(row)

    return sessions, times


def ema_state(row):
    a, b, c = (
        row["ema9"], row["ema21"], row["ema55"]
    )

    if any(x is None for x in (a, b, c)):
        return "UNAVAILABLE"
    if a > b > c:
        return "BULLISH_STACK"
    if a < b < c:
        return "BEARISH_STACK"
    return "MIXED"


def vwap_state(row):
    if row["vwap"] is None:
        return "UNAVAILABLE"
    if row["close"] > row["vwap"]:
        return "ABOVE"
    if row["close"] < row["vwap"]:
        return "BELOW"
    return "AT"


def main():
    print("\n=== JAHVASCRIPT MARKET STATE INTEGRATION ===")

    require(
        "journal/" in (
            ROOT / ".gitignore"
        ).read_text().splitlines(),
        "Journal must be gitignored"
    )
    require(not OUTPUT.exists(), "V001 output already exists")

    dataset_bytes = DATASET.read_bytes()
    research_bytes = RESEARCH.read_bytes()
    clock_bytes = CLOCK.read_bytes()

    dataset_hash = digest(dataset_bytes)
    research_hash = digest(research_bytes)
    clock_hash = digest(clock_bytes)

    require(
        dataset_hash == EXPECTED_DATASET_HASH,
        "V001 dataset fingerprint mismatch"
    )

    dataset = json.loads(dataset_bytes)
    research = json.loads(research_bytes)
    clock = json.loads(clock_bytes)

    require(
        dataset.get("dataset_version") == "M5_SPY_QQQ_v001"
        and dataset.get("historical_research_only") is True
        and dataset.get("execution_enabled") is False
        and dataset.get("trade_authorization") is False,
        "Unsafe dataset"
    )

    require(
        research.get("dataset_sha256") == dataset_hash
        and research.get("out_of_sample") is False
        and research.get("execution_enabled") is False
        and research.get("trade_authorization") is False
        and research.get("lookback_bars") == LOOKBACK
        and research.get("confirmation_horizon_bars") == HORIZON,
        "Unexpected breakout research configuration"
    )

    require(
        clock.get("dataset_sha256") == dataset_hash
        and clock.get("research_sha256") == research_hash
        and clock.get("eligible_events") == 239
        and clock.get("historical_research_only") is True
        and clock.get("execution_enabled") is False
        and clock.get("trade_authorization") is False,
        "Unexpected RVOL research configuration"
    )

    dates = sorted(dataset["full_standard_session_dates"])
    require(len(dates) == 17, "Unexpected session dates")

    sessions = {}
    timelines = {}

    for symbol in ("SPY", "QQQ"):
        sessions[symbol], timelines[symbol] = prepare(
            symbol, dataset
        )

        for day in dates:
            bars = sessions[symbol][day]

            require(
                len(bars) == 78
                and bars[0]["slot"] == "09:30"
                and bars[-1]["slot"] == "15:55"
                and all(
                    b["time"] - a["time"]
                    == timedelta(minutes=5)
                    for a, b in zip(bars, bars[1:])
                ),
                symbol + ": incomplete historical session"
            )

    require(
        timelines["SPY"] == timelines["QQQ"],
        "SPY/QQQ timestamps misaligned"
    )

    original_events = {}

    for symbol in ("SPY", "QQQ"):
        for event in research["symbols"][symbol]["events"]:
            key = (
                symbol,
                datetime.fromisoformat(
                    event["breakout_utc"]
                ).astimezone(timezone.utc)
            )

            require(
                key not in original_events
                and event["trade_signal"] is False,
                "Duplicate or unsafe breakout"
            )
            original_events[key] = event

    require(
        len(original_events) == 328,
        "Original breakout count changed"
    )

    integrated = []
    states = Counter()
    symbols = Counter()

    for item in clock["events"]:
        symbol = item["symbol"]
        require(symbol in sessions, "Unknown symbol")

        dt = datetime.fromisoformat(
            item["timestamp"]
        ).astimezone(timezone.utc)

        day = item["day"]
        require(day in dates, "Unknown session date")

        bars = sessions[symbol][day]
        matches = [
            i for i, bar in enumerate(bars)
            if bar["time"] == dt
        ]

        require(
            len(matches) == 1,
            "Breakout candle not found"
        )

        i = matches[0]
        require(
            LOOKBACK <= i
            and i + HORIZON < len(bars),
            "Invalid breakout analysis window"
        )

        current = bars[i]
        prior = bars[i-LOOKBACK:i]

        support = min(bar["low"] for bar in prior)
        resistance = max(bar["high"] for bar in prior)
        previous_close = bars[i-1]["close"]

        up = (
            previous_close <= resistance
            and current["close"] > resistance
        )
        down = (
            previous_close >= support
            and current["close"] < support
        )

        require(
            up or down,
            "Saved event not a qualifying breakout"
        )

        direction = "UP" if up else "DOWN"

        original = original_events.get((symbol, dt))

        require(
            original is not None
            and original["direction"] == direction
            and original["outcome"] == item["outcome"]
            and original["date"] == day
            and original["cohort"] == item["cohort"],
            "Breakout research alignment failed"
        )

        state_ema = ema_state(current)
        state_vwap = vwap_state(current)

        require(
            state_ema == original["ema_alignment"]
            and state_vwap == original["vwap_position"],
            "EMA/VWAP alignment failed"
        )

        day_index = dates.index(day)
        require(
            day_index >= REFERENCE_DAYS,
            "Insufficient prior trading sessions"
        )

        prior_days = dates[
            day_index-REFERENCE_DAYS:day_index
        ]

        require(
            item["reference_days"] == prior_days
            and all(d < day for d in prior_days),
            "Clock baseline includes future data"
        )

        slot = current["slot"]
        clock_volumes = []

        for prior_day in prior_days:
            matches = [
                bar["volume"]
                for bar in sessions[symbol][prior_day]
                if bar["slot"] == slot
            ]
            require(
                len(matches) == 1,
                "Clock reference unavailable"
            )
            clock_volumes.append(matches[0])

        features = calculate_dual_rvol(
            current["volume"],
            [
                bar["volume"]
                for bar in bars[i-5:i]
            ],
            clock_volumes
        )

        require(
            features["rolling_group"]
            == item["rolling_group"]
            and features["clock_group"]
            == item["clock_group"]
            and features["trade_signal"] is False,
            "Dual-RVOL classification mismatch"
        )

        for field in ("rolling_rvol", "clock_rvol"):
            require(
                abs(
                    features[field]
                    - Decimal(str(item[field]))
                ) <= Decimal("0.000051"),
                "Dual-RVOL numerical mismatch"
            )

        integrated.append({
            "symbol": symbol,
            "session_date": day,
            "breakout_time_utc": dt.isoformat(),
            "known_at_breakout": {
                "direction": direction,
                "close": str(current["close"]),
                "support": str(support),
                "resistance": str(resistance),
                "ema9": str(current["ema9"]),
                "ema21": str(current["ema21"]),
                "ema55": str(current["ema55"]),
                "ema_alignment": state_ema,
                "session_vwap": (
                    str(current["vwap"])
                    if current["vwap"] is not None
                    else None
                ),
                "vwap_position": state_vwap,
                "rolling_rvol": str(
                    features["rolling_rvol"]
                ),
                "clock_rvol": str(
                    features["clock_rvol"]
                ),
                "rvol_agreement": features[
                    "agreement_state"
                ],
                "clock_reference_dates": prior_days
            },
            "retrospective_outcome": {
                "classification": original["outcome"],
                "confirmation_time_utc": original[
                    "confirmation_utc"
                ]
            },
            "research_only": True,
            "trade_signal": False
        })

        symbols[symbol] += 1
        states[features["agreement_state"]] += 1

    require(
        len(integrated) == 239
        and symbols == Counter({"SPY": 124, "QQQ": 115}),
        "Integrated event totals mismatch"
    )

    expected_states = {
        "HIGH_TO_HIGH": 73,
        "HIGH_TO_LOW_NORMAL": 46,
        "LOW_NORMAL_TO_HIGH": 41,
        "LOW_NORMAL_TO_LOW_NORMAL": 79
    }

    require(
        dict(states) == expected_states,
        "RVOL state totals mismatch"
    )

    report = {
        "generated_utc": datetime.now(
            timezone.utc
        ).isoformat(),
        "dataset_version": "M5_SPY_QQQ_v001",
        "dataset_sha256": dataset_hash,
        "expanded_research_sha256": research_hash,
        "clock_rvol_sha256": clock_hash,
        "events_integrated": len(integrated),
        "by_symbol": dict(symbols),
        "rvol_agreement_counts": dict(states),
        "events": integrated,
        "features_use_future_candles": False,
        "outcomes_are_retrospective": True,
        "out_of_sample": False,
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
        digest(DATASET.read_bytes()) == dataset_hash
        and digest(RESEARCH.read_bytes()) == research_hash
        and digest(CLOCK.read_bytes()) == clock_hash,
        "A research source changed"
    )

    print("Historical breakout events integrated:", len(integrated))
    print("SPY events:", symbols["SPY"])
    print("QQQ events:", symbols["QQQ"])

    print("\nDUAL-RVOL AGREEMENT STATES")
    for name, count in expected_states.items():
        print(name + ":", states[name])

    print("\nMarket-structure levels: VERIFIED")
    print("EMA 9/21/55 integration: PASS")
    print("Session VWAP integration: PASS")
    print("Dual-RVOL feature integration: PASS")
    print("Historical outcome separation: PASS")
    print("SPY/QQQ source alignment: PASS")
    print("Original files and reports: UNCHANGED")
    print("Source prices independently verified: NO")
    print("Predictive validity established: NO")
    print("Saved: journal/market_state_v001.json")

    print("\nMARKET STATE INTEGRATION PASSED")
    print("WEBULL REQUESTS: NONE")
    print("AI REQUESTS: NONE")
    print("ORDER EXECUTION: DISABLED")


if __name__ == "__main__":
    main()
