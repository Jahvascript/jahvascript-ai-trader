"""Independent point-in-time market-state audit.
Offline, read-only, historical research only.
"""

import hashlib
import json
from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parent
JOURNAL = ROOT / "journal"
DATASET = JOURNAL / "datasets/M5_SPY_QQQ_v001.json"
STATE = JOURNAL / "market_state_v001.json"
RESEARCH = JOURNAL / "expanded_research_v001.json"
CLOCK = JOURNAL / "rvol_clock_normalized_v001.json"

ET = ZoneInfo("America/New_York")
TOLERANCE = Decimal("0.000000000001")
HORIZON = 3
LOOKBACK = 12
PRIOR_DAYS = 5

FEATURE_KEYS = {
    "direction", "close", "support", "resistance",
    "ema9", "ema21", "ema55", "ema_alignment",
    "session_vwap", "vwap_position",
    "rolling_rvol", "clock_rvol", "rvol_agreement",
    "clock_reference_dates"
}


def require(ok, message):
    if not ok:
        raise ValueError("LEAKAGE AUDIT FAILED: " + message)


def parse(value):
    dt = datetime.fromisoformat(value)
    require(
        dt.tzinfo is not None and dt.utcoffset() is not None,
        "Timestamp missing timezone"
    )
    return dt.astimezone(timezone.utc)


def decimal_equal(stored, expected, name):
    require(stored is not None, name + ": missing value")
    difference = abs(Decimal(str(stored)) - expected)
    require(
        difference <= TOLERANCE,
        name + ": numerical mismatch"
    )
    return difference


def independent_ema(prices, period):
    """Recalculate using only prices through this candle."""
    require(
        len(prices) >= period,
        "Insufficient EMA warmup"
    )

    previous = sum(prices[:period], Decimal(0)) / period
    weight = Decimal(2) / Decimal(period + 1)

    for price in prices[period:]:
        previous = (
            weight * price
            + (Decimal(1) - weight) * previous
        )

    return previous


def prepare(dataset):
    full_dates = sorted(
        dataset["full_standard_session_dates"]
    )
    require(len(full_dates) == 17, "Session coverage changed")

    timelines = {}
    sessions = {}
    prefixes = {}

    for symbol in ("SPY", "QQQ"):
        bars = dataset["symbols"][symbol]["bars"]
        require(len(bars) == 1400, "Candle count changed")

        all_rows = []
        by_day = defaultdict(list)

        for index, bar in enumerate(bars):
            dt = parse(bar["time"])
            local = dt.astimezone(ET)

            values = {
                k: Decimal(str(bar[k]))
                for k in (
                    "open", "high", "low",
                    "close", "volume"
                )
            }

            o, h, l, c, v = (
                values[k]
                for k in (
                    "open", "high", "low",
                    "close", "volume"
                )
            )

            require(
                all(x.is_finite() for x in values.values())
                and min(o, h, l, c) > 0
                and l <= min(o, c)
                and h >= max(o, c)
                and v >= 0
                and bar["session"] == "RTH",
                "Invalid historical OHLCV"
            )

            row = {
                **values,
                "dt": dt,
                "day": local.date().isoformat(),
                "slot": local.strftime("%H:%M"),
                "global_index": index
            }

            all_rows.append(row)
            by_day[row["day"]].append(row)

        times = [r["dt"] for r in all_rows]

        require(
            times == sorted(times)
            and len(times) == len(set(times)),
            "Unordered or duplicated candles"
        )

        for day in full_dates:
            rows = by_day[day]

            require(
                len(rows) == 78
                and rows[0]["slot"] == "09:30"
                and rows[-1]["slot"] == "15:55"
                and all(
                    b["dt"] - a["dt"]
                    == timedelta(minutes=5)
                    for a, b in zip(rows, rows[1:])
                ),
                "Incomplete research session"
            )

        timelines[symbol] = times
        sessions[symbol] = by_day
        prefixes[symbol] = all_rows

    require(
        timelines["SPY"] == timelines["QQQ"],
        "SPY/QQQ timestamp mismatch"
    )

    return full_dates, sessions, prefixes


def audit_event(event, full_dates, sessions, prefixes):
    symbol = event["symbol"]
    require(symbol in ("SPY", "QQQ"), "Unknown symbol")

    day = event["session_date"]
    dt = parse(event["breakout_time_utc"])
    require(
        dt.astimezone(ET).date().isoformat() == day,
        "Breakout date mismatch"
    )

    rows = sessions[symbol][day]
    matches = [
        i for i, row in enumerate(rows)
        if row["dt"] == dt
    ]

    require(len(matches) == 1, "Event candle missing")
    i = matches[0]

    require(
        i >= LOOKBACK and i + HORIZON < len(rows),
        "Event lacks complete future outcome window"
    )

    current = rows[i]
    features = event["known_at_breakout"]
    outcome = event["retrospective_outcome"]

    require(
        set(features) == FEATURE_KEYS,
        "Unexpected feature fields"
    )
    require(
        set(outcome) == {
            "classification", "confirmation_time_utc"
        },
        "Unexpected outcome fields"
    )
    require(
        event["research_only"] is True
        and event["trade_signal"] is False,
        "Unsafe event flags"
    )

    earlier = rows[i-LOOKBACK:i]
    support = min(row["low"] for row in earlier)
    resistance = max(row["high"] for row in earlier)
    prior_close = rows[i-1]["close"]
    close = current["close"]

    bullish = prior_close <= resistance < close
    bearish = prior_close >= support > close

    require(
        bullish != bearish,
        "Saved event is not a unique breakout"
    )

    direction = "UP" if bullish else "DOWN"

    require(
        features["direction"] == direction,
        "Breakout direction mismatch"
    )

    errors = [
        decimal_equal(features["close"], close, "Close"),
        decimal_equal(
            features["support"], support, "Support"
        ),
        decimal_equal(
            features["resistance"], resistance, "Resistance"
        )
    ]

    # Recompute EMAs from a price prefix that ends
    # precisely at the breakout candle.
    cutoff = current["global_index"] + 1
    past = prefixes[symbol][:cutoff]
    past_closes = [row["close"] for row in past]

    ema_values = {}

    for period in (9, 21, 55):
        expected = independent_ema(past_closes, period)
        ema_values[period] = expected
        errors.append(
            decimal_equal(
                features[f"ema{period}"],
                expected,
                f"EMA {period}"
            )
        )

    a, b, c = (
        ema_values[9], ema_values[21], ema_values[55]
    )

    ema_label = (
        "BULLISH_STACK" if a > b > c
        else "BEARISH_STACK" if a < b < c
        else "MIXED"
    )

    require(
        features["ema_alignment"] == ema_label,
        "EMA alignment mismatch"
    )

    # Session VWAP uses only the session candles
    # through the breakout, never later bars.
    session_prefix = rows[:i+1]
    total_volume = sum(
        (row["volume"] for row in session_prefix),
        Decimal(0)
    )
    weighted = sum(
        (
            ((row["high"] + row["low"] + row["close"]) / 3)
            * row["volume"]
            for row in session_prefix
        ),
        Decimal(0)
    )

    vwap = (
        weighted / total_volume
        if total_volume > 0 else None
    )

    if vwap is None:
        require(
            features["session_vwap"] is None
            and features["vwap_position"] == "UNAVAILABLE",
            "VWAP should be unavailable"
        )
    else:
        errors.append(
            decimal_equal(
                features["session_vwap"], vwap, "VWAP"
            )
        )
        label = (
            "ABOVE" if close > vwap
            else "BELOW" if close < vwap
            else "AT"
        )
        require(
            features["vwap_position"] == label,
            "VWAP position mismatch"
        )

    # Rolling volume uses five earlier SAME-DAY bars.
    previous_volumes = [
        row["volume"] for row in rows[i-5:i]
    ]
    rolling_base = (
        sum(previous_volumes, Decimal(0)) / 5
    )
    require(rolling_base > 0, "Rolling baseline zero")

    rolling = current["volume"] / rolling_base

    # Clock RVOL uses five complete prior sessions.
    day_index = full_dates.index(day)
    require(
        day_index >= PRIOR_DAYS,
        "Insufficient previous sessions"
    )

    earlier_dates = full_dates[
        day_index-PRIOR_DAYS:day_index
    ]

    require(
        features["clock_reference_dates"] == earlier_dates
        and all(d < day for d in earlier_dates),
        "Clock baseline includes future dates"
    )

    clock_volumes = []

    for previous_day in earlier_dates:
        matches = [
            row["volume"]
            for row in sessions[symbol][previous_day]
            if row["slot"] == current["slot"]
        ]
        require(
            len(matches) == 1,
            "Clock-time baseline missing"
        )
        clock_volumes.append(matches[0])

    clock_base = sum(clock_volumes, Decimal(0)) / 5
    require(clock_base > 0, "Clock baseline zero")

    clock = current["volume"] / clock_base

    errors.extend([
        decimal_equal(
            features["rolling_rvol"], rolling,
            "Rolling RVOL"
        ),
        decimal_equal(
            features["clock_rvol"], clock,
            "Clock-time RVOL"
        )
    ])

    rolling_group = (
        "HIGH" if rolling >= Decimal("1.2")
        else "LOW_NORMAL"
    )
    clock_group = (
        "HIGH" if clock >= Decimal("1.2")
        else "LOW_NORMAL"
    )

    state = rolling_group + "_TO_" + clock_group

    require(
        features["rvol_agreement"] == state,
        "Dual-RVOL agreement mismatch"
    )

    # Only the outcome check below may use later bars.
    broken_level = resistance if bullish else support

    reversal = next(
        (
            row for row in rows[i+1:i+1+HORIZON]
            if (
                row["close"] < broken_level
                if bullish
                else row["close"] > broken_level
            )
        ),
        None
    )

    classification = (
        "CONFIRMED_REVERSAL"
        if reversal is not None
        else "NO_FAILURE_WITHIN_HORIZON"
    )

    confirmation = (
        reversal["dt"] if reversal is not None else None
    )

    require(
        outcome["classification"] == classification,
        "Retrospective outcome mismatch"
    )

    stored_confirmation = outcome["confirmation_time_utc"]

    require(
        (
            parse(stored_confirmation)
            if stored_confirmation is not None
            else None
        ) == confirmation,
        "Confirmation timestamp mismatch"
    )

    return state, classification, max(errors)


def main():
    print("\n=== JAHVASCRIPT MARKET-STATE LEAKAGE AUDIT ===")

    require(
        "journal/" in (
            ROOT / ".gitignore"
        ).read_text().splitlines(),
        "Journal must be gitignored"
    )

    paths = (DATASET, STATE, RESEARCH, CLOCK)

    before = {
        str(path): hashlib.sha256(
            path.read_bytes()
        ).hexdigest()
        for path in paths
    }

    dataset = json.loads(DATASET.read_text())
    state = json.loads(STATE.read_text())

    require(
        dataset.get("historical_research_only") is True
        and dataset.get("execution_enabled") is False
        and dataset.get("trade_authorization") is False
        and state.get("historical_research_only") is True
        and state.get("features_use_future_candles") is False
        and state.get("outcomes_are_retrospective") is True
        and state.get("out_of_sample") is False
        and state.get("execution_enabled") is False
        and state.get("trade_authorization") is False,
        "Unsafe report configuration"
    )

    require(
        state["dataset_sha256"] == before[str(DATASET)]
        and state["expanded_research_sha256"]
            == before[str(RESEARCH)]
        and state["clock_rvol_sha256"]
            == before[str(CLOCK)],
        "Input fingerprint mismatch"
    )

    events = state["events"]
    require(
        len(events) == 239
        and state["events_integrated"] == 239,
        "Integrated count changed"
    )

    full_dates, sessions, prefixes = prepare(dataset)
    seen = set()
    states = Counter()
    outcomes = Counter()
    symbols = Counter()
    maximum_error = Decimal(0)

    for event in events:
        key = (
            event["symbol"],
            parse(event["breakout_time_utc"])
        )
        require(key not in seen, "Duplicate event")
        seen.add(key)

        agreement, outcome, error = audit_event(
            event, full_dates, sessions, prefixes
        )

        states[agreement] += 1
        outcomes[outcome] += 1
        symbols[event["symbol"]] += 1
        maximum_error = max(maximum_error, error)

    require(
        symbols == Counter({"SPY": 124, "QQQ": 115}),
        "Symbol counts differ"
    )

    require(
        dict(states) == state["rvol_agreement_counts"],
        "Agreement counts differ"
    )

    require(
        states == Counter({
            "HIGH_TO_HIGH": 73,
            "HIGH_TO_LOW_NORMAL": 46,
            "LOW_NORMAL_TO_HIGH": 41,
            "LOW_NORMAL_TO_LOW_NORMAL": 79
        }),
        "Historical agreement counts changed"
    )

    after = {
        str(path): hashlib.sha256(
            path.read_bytes()
        ).hexdigest()
        for path in paths
    }
    require(before == after, "Protected file changed")

    print("Historical events independently verified:", len(seen))
    print("SPY events:", symbols["SPY"])
    print("QQQ events:", symbols["QQQ"])
    print("Maximum numerical difference:", maximum_error)

    print("\nOUTCOME RECONCILIATION")
    for name, count in sorted(outcomes.items()):
        print(name + ":", count)

    print("\nPoint-in-time EMA prefixes: PASS")
    print("Session-to-breakout VWAP: PASS")
    print("Past-only RVOL baselines: PASS")
    print("Breakout levels from prior candles: PASS")
    print("Outcomes independently verified: PASS")
    print("Source/report hashes unchanged: PASS")

    print("\nIMPORTANT RESEARCH RESTRICTION")
    print("Full-future-horizon event selection: YES")
    print("Suitable as live prediction feed: NO")
    print("Independent source-price verification: NO")
    print("Untouched out-of-sample evaluation: NO")

    print("\nMARKET STATE LEAKAGE AUDIT PASSED")
    print("HISTORICAL/RESEARCH DATA FILES MODIFIED: NONE")
    print("WEBULL REQUESTS: NONE")
    print("AI REQUESTS: NONE")
    print("ORDER EXECUTION: DISABLED")


if __name__ == "__main__":
    main()
