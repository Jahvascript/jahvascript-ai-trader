"""Independent JahvaScript replay timeline verification.

Reconstructs breakouts, point-in-time features, and event
lifecycles from source candles without importing the replay
engine. Offline, read-only research.
"""

import hashlib
import json
import os
from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parent
JOURNAL = ROOT / "journal"
DATASET = JOURNAL / "datasets/M5_SPY_QQQ_v001.json"
REPLAY = JOURNAL / "market_replay_v001.json"
OUTPUT = JOURNAL / "market_replay_timeline_audit_v001.json"

ET = ZoneInfo("America/New_York")
STEP = timedelta(minutes=5)
LOOKBACK = 12
HORIZON = 3
TOL = Decimal("0.000000000001")
HIGH = Decimal("1.2")

EXPECTED_HASH = (
    "4c55e6c7961e9210f73f1468b20498fe20db4941cd27b10bd11753336292e3cb"
)

FEATURE_KEYS = {
    "direction", "close", "support", "resistance",
    "ema9", "ema21", "ema55", "ema_alignment",
    "session_vwap", "vwap_position",
    "rolling_rvol", "clock_rvol",
    "rvol_agreement", "clock_reference_dates"
}

FINAL_STATES = {
    "CONFIRMED_REVERSAL",
    "NO_FAILURE_WITHIN_HORIZON",
    "CENSORED_SESSION_END"
}


def require(ok, message):
    if not ok:
        raise ValueError("TIMELINE AUDIT FAILED: " + message)


def parse(value):
    dt = datetime.fromisoformat(value)
    require(
        dt.tzinfo is not None and dt.utcoffset() is not None,
        "Timestamp lacks timezone"
    )
    return dt.astimezone(timezone.utc)


def same_number(actual, expected, label):
    require(actual is not None, label + " missing")

    value = Decimal(str(actual))

    require(
        value.is_finite()
        and abs(value - expected) <= TOL,
        label + " differs from source candles"
    )


def ema_at(prices, period):
    require(len(prices) >= period, "Insufficient EMA bars")
    value = sum(prices[:period], Decimal(0)) / period
    alpha = Decimal(2) / Decimal(period + 1)

    for price in prices[period:]:
        value = value + alpha * (price - value)

    return value


def volume_state(current, baseline):
    avg = sum(baseline, Decimal(0)) / Decimal(
        len(baseline)
    )

    if avg <= 0:
        return None, "UNAVAILABLE"

    result = current / avg

    return (
        result,
        "HIGH" if result >= HIGH else "LOW_NORMAL"
    )


def load_candles(dataset, dates):
    all_rows = {}
    sessions = {}

    for symbol in ("SPY", "QQQ"):
        rows = []
        by_day = defaultdict(list)

        for source in dataset["symbols"][symbol]["bars"]:
            dt = parse(source["time"])
            local = dt.astimezone(ET)

            numbers = {
                field: Decimal(str(source[field]))
                for field in (
                    "open", "high", "low",
                    "close", "volume"
                )
            }

            o, h, l, c, v = (
                numbers[field]
                for field in (
                    "open", "high", "low",
                    "close", "volume"
                )
            )

            require(
                source.get("session") == "RTH"
                and all(x.is_finite() for x in numbers.values())
                and min(o, h, l, c) > 0
                and l <= min(o, c)
                and h >= max(o, c)
                and v >= 0,
                "Invalid historical candle"
            )

            row = {
                **numbers,
                "start": dt,
                "close_time": dt + STEP,
                "date": local.date().isoformat(),
                "slot": local.strftime("%H:%M"),
                "global_index": len(rows)
            }

            rows.append(row)
            by_day[row["date"]].append(row)

        timestamps = [row["start"] for row in rows]

        require(
            len(rows) == 1400
            and timestamps == sorted(timestamps)
            and len(timestamps) == len(set(timestamps)),
            symbol + ": source timeline invalid"
        )

        for day in dates:
            candles = by_day[day]

            require(
                len(candles) == 78
                and candles[0]["slot"] == "09:30"
                and candles[-1]["slot"] == "15:55"
                and all(
                    b["start"] - a["start"] == STEP
                    for a, b in zip(
                        candles, candles[1:]
                    )
                ),
                symbol + ": incomplete session"
            )

        all_rows[symbol] = rows
        sessions[symbol] = by_day

    require(
        [r["start"] for r in all_rows["SPY"]]
        == [r["start"] for r in all_rows["QQQ"]],
        "SPY/QQQ timestamps differ"
    )

    return all_rows, sessions


def expected_breakouts(symbol, sessions, dates):
    expected = {}

    for day in dates:
        bars = sessions[day]

        for i in range(LOOKBACK, len(bars)):
            previous = bars[i-LOOKBACK:i]
            ceiling = max(x["high"] for x in previous)
            floor = min(x["low"] for x in previous)
            close_before = bars[i-1]["close"]
            close_now = bars[i]["close"]

            up = close_before <= ceiling < close_now
            down = close_before >= floor > close_now

            require(not (up and down), "Ambiguous breakout")

            if not (up or down):
                continue

            key = (
                symbol + ":"
                + bars[i]["start"].isoformat()
            )

            expected[key] = {
                "symbol": symbol,
                "day": day,
                "index": i,
                "direction": "UP" if up else "DOWN",
                "support": floor,
                "resistance": ceiling,
                "broken_level": ceiling if up else floor
            }

    return expected


def audit_features(
    event, expected, bars, all_rows,
    sessions, dates
):
    i = expected["index"]
    current = bars[i]
    features = event["features"]

    require(
        set(features) == FEATURE_KEYS,
        "Unexpected feature fields"
    )

    require(
        event["trade_signal"] is False,
        "Trading signal unexpectedly enabled"
    )

    require(
        features["direction"] == expected["direction"],
        "Breakout direction incorrect"
    )

    same_number(
        event["broken_level"],
        expected["broken_level"],
        "Broken level"
    )

    for name, value in (
        ("close", current["close"]),
        ("support", expected["support"]),
        ("resistance", expected["resistance"])
    ):
        same_number(features[name], value, name)

    cutoff = current["global_index"] + 1
    closes = [
        row["close"]
        for row in all_rows[:cutoff]
    ]

    ema_values = {}

    for period in (9, 21, 55):
        value = ema_at(closes, period)
        ema_values[period] = value

        same_number(
            features["ema" + str(period)],
            value,
            "EMA " + str(period)
        )

    a, b, c = (
        ema_values[9],
        ema_values[21],
        ema_values[55]
    )

    expected_alignment = (
        "BULLISH_STACK" if a > b > c
        else "BEARISH_STACK" if a < b < c
        else "MIXED"
    )

    require(
        features["ema_alignment"] == expected_alignment,
        "EMA alignment incorrect"
    )

    session_prefix = bars[:i+1]

    volume_total = sum(
        (r["volume"] for r in session_prefix),
        Decimal(0)
    )

    weighted_total = sum(
        (
            (r["high"] + r["low"] + r["close"])
            / Decimal(3) * r["volume"]
            for r in session_prefix
        ),
        Decimal(0)
    )

    vwap = (
        weighted_total / volume_total
        if volume_total > 0 else None
    )

    if vwap is None:
        require(
            features["session_vwap"] is None
            and features["vwap_position"] == "UNAVAILABLE",
            "Unavailable VWAP mismatch"
        )
    else:
        same_number(
            features["session_vwap"], vwap,
            "Session VWAP"
        )

        position = (
            "ABOVE" if current["close"] > vwap
            else "BELOW" if current["close"] < vwap
            else "AT"
        )

        require(
            features["vwap_position"] == position,
            "VWAP position incorrect"
        )

    rolling, rolling_group = volume_state(
        current["volume"],
        [r["volume"] for r in bars[i-5:i]]
    )

    if rolling is None:
        require(
            features["rolling_rvol"] is None,
            "Unexpected rolling RVOL"
        )
    else:
        same_number(
            features["rolling_rvol"],
            rolling,
            "Rolling RVOL"
        )

    day_index = dates.index(expected["day"])

    reference_days = (
        dates[day_index-5:day_index]
        if day_index >= 5
        else []
    )

    require(
        features["clock_reference_dates"]
        == reference_days,
        "Clock reference dates incorrect"
    )

    clock = None
    clock_group = "UNAVAILABLE"

    if reference_days:
        volumes = []

        for reference_day in reference_days:
            matching = [
                row["volume"]
                for row in sessions[reference_day]
                if row["slot"] == current["slot"]
            ]

            require(
                len(matching) == 1
                and reference_day < expected["day"],
                "Clock reference not strictly historical"
            )

            volumes.append(matching[0])

        clock, clock_group = volume_state(
            current["volume"], volumes
        )

    if clock is None:
        require(
            features["clock_rvol"] is None,
            "Clock RVOL should be unavailable"
        )
    else:
        same_number(
            features["clock_rvol"],
            clock,
            "Clock-time RVOL"
        )

    require(
        features["rvol_agreement"]
        == rolling_group + "_TO_" + clock_group,
        "RVOL state incorrect"
    )


def expected_outcome(expected, bars):
    i = expected["index"]
    direction = expected["direction"]
    level = expected["broken_level"]

    for j in range(i + 1, min(i + HORIZON + 1, len(bars))):
        failed = (
            bars[j]["close"] < level
            if direction == "UP"
            else bars[j]["close"] > level
        )

        if failed:
            return (
                "CONFIRMED_REVERSAL",
                bars[j]["close_time"],
                bars[j]["start"]
            )

    if i + HORIZON < len(bars):
        return (
            "NO_FAILURE_WITHIN_HORIZON",
            bars[i + HORIZON]["close_time"],
            None
        )

    return (
        "CENSORED_SESSION_END",
        bars[-1]["close_time"],
        None
    )


def audit_symbol(
    symbol, replay, expected,
    all_rows, sessions, dates
):
    section = replay["symbols"][symbol]
    events = section["events"]
    transitions = section["transitions"]

    ids = [event["id"] for event in events]

    require(
        len(ids) == len(set(ids))
        and set(ids) == set(expected),
        symbol + ": observed breakout set differs"
    )

    lifecycle = defaultdict(list)
    previous_transition_time = None

    for change in transitions:
        event_id = change["id"]
        at = parse(change["at_utc"])
        state = change["state"]

        require(
            event_id in expected
            and state in FINAL_STATES | {"BREAKOUT_OBSERVED"},
            "Unknown timeline transition"
        )

        require(
            previous_transition_time is None
            or at >= previous_transition_time,
            "Timeline transitions out of order"
        )

        previous_transition_time = at
        lifecycle[event_id].append((state, at))

    outcomes = Counter()
    same_time_censor = 0

    for event in events:
        event_id = event["id"]
        ref = expected[event_id]
        bars = sessions[ref["day"]]
        candle = bars[ref["index"]]

        observed = parse(event["observed_at_utc"])
        terminal = parse(event["outcome_known_at_utc"])
        candle_start = parse(
            event["breakout_candle_start_utc"]
        )

        require(
            event["symbol"] == symbol
            and event["session_date"] == ref["day"]
            and event["index"] == ref["index"]
            and candle_start == candle["start"]
            and observed == candle["close_time"],
            "Breakout observation timing mismatch"
        )

        audit_features(
            event, ref, bars,
            all_rows, sessions, dates
        )

        outcome, expected_terminal, confirmation = (
            expected_outcome(ref, bars)
        )

        actual_confirmation = (
            parse(event["confirmation_candle_start_utc"])
            if event["confirmation_candle_start_utc"]
            is not None
            else None
        )

        require(
            event["outcome"] == outcome
            and terminal == expected_terminal
            and actual_confirmation == confirmation,
            "Outcome timing or classification mismatch"
        )

        require(
            terminal >= observed,
            "Outcome predates breakout observation"
        )

        if outcome != "CENSORED_SESSION_END":
            require(
                terminal > observed,
                "Later-candle outcome known too early"
            )
        elif terminal == observed:
            same_time_censor += 1

        require(
            lifecycle[event_id] == [
                ("BREAKOUT_OBSERVED", observed),
                (outcome, terminal)
            ],
            "Missing, duplicate or premature transition"
        )

        outcomes[outcome] += 1

    require(
        len(transitions) == 2 * len(events)
        and len(lifecycle) == len(events)
        and dict(outcomes) == section["outcome_counts"],
        "State-transition totals inconsistent"
    )

    print("\n---", symbol, "---")
    print("Breakout observations:", len(events))
    print("Transitions independently checked:", len(transitions))
    print("Confirmed reversals:", outcomes["CONFIRMED_REVERSAL"])
    print(
        "Full horizon without reversal:",
        outcomes["NO_FAILURE_WITHIN_HORIZON"]
    )
    print(
        "Session-end censored:",
        outcomes["CENSORED_SESSION_END"]
    )
    print(
        "Same-time observation/censoring:",
        same_time_censor
    )
    print("Event lifecycle timing: PASS")
    print("Full feature reconstruction: PASS")

    return outcomes, len(transitions)


def main():
    print("\n=== JAHVASCRIPT REPLAY TIMELINE AUDIT ===")

    require(
        "journal/" in (
            ROOT / ".gitignore"
        ).read_text().splitlines(),
        "Journal must be gitignored"
    )

    require(
        not OUTPUT.exists(),
        "V001 audit output exists; refusing overwrite"
    )

    before = {
        str(path): hashlib.sha256(
            path.read_bytes()
        ).hexdigest()
        for path in (DATASET, REPLAY)
    }

    require(
        before[str(DATASET)] == EXPECTED_HASH,
        "Historical dataset fingerprint mismatch"
    )

    dataset = json.loads(DATASET.read_text())
    replay = json.loads(REPLAY.read_text())

    require(
        dataset.get("historical_research_only") is True
        and dataset.get("execution_enabled") is False
        and dataset.get("trade_authorization") is False
        and replay.get("dataset_sha256") == EXPECTED_HASH
        and replay.get("out_of_sample") is False
        and replay.get("trade_signal") is False
        and replay.get("execution_enabled") is False
        and replay.get("trade_authorization") is False
        and replay.get("replay_is_historical_not_live") is True
        and replay.get(
            "breakouts_selected_without_future_outcomes"
        ) is True,
        "Unexpected research safety configuration"
    )

    dates = sorted(dataset["full_standard_session_dates"])

    require(
        len(dates) == 17
        and replay["complete_sessions_replayed"] == 17
        and replay["confirmation_horizon_candles"] == HORIZON
        and replay[
            "prior_complete_sessions_for_clock_rvol"
        ] == 5,
        "Replay configuration changed"
    )

    all_rows, sessions = load_candles(dataset, dates)

    totals = Counter()
    transition_count = 0

    for symbol in ("SPY", "QQQ"):
        expected = expected_breakouts(
            symbol, sessions[symbol], dates
        )

        outcomes, transitions = audit_symbol(
            symbol, replay, expected,
            all_rows[symbol], sessions[symbol], dates
        )

        totals.update(outcomes)
        transition_count += transitions

    require(
        totals == Counter({
            "CONFIRMED_REVERSAL": 155,
            "NO_FAILURE_WITHIN_HORIZON": 155,
            "CENSORED_SESSION_END": 18
        }),
        "Combined historical outcomes mismatch"
    )

    require(
        transition_count == 656,
        "Expected 656 lifecycle transitions"
    )

    after = {
        str(path): hashlib.sha256(
            path.read_bytes()
        ).hexdigest()
        for path in (DATASET, REPLAY)
    }

    require(before == after, "Protected source changed")

    report = {
        "generated_utc": datetime.now(
            timezone.utc
        ).isoformat(),
        "dataset_sha256": before[str(DATASET)],
        "replay_sha256": before[str(REPLAY)],
        "events_verified": 328,
        "transitions_verified": transition_count,
        "outcomes": dict(totals),
        "independent_breakout_discovery": True,
        "feature_prefix_reconstruction": True,
        "event_timing_verified": True,
        "research_only": True,
        "historical_replay_not_live": True,
        "out_of_sample": False,
        "source_prices_verified": False,
        "predictive_validity_established": False,
        "trade_signal": False,
        "execution_enabled": False,
        "trade_authorization": False
    }

    os.umask(0o077)

    with OUTPUT.open("x") as handle:
        json.dump(report, handle, indent=2)
        handle.write("\n")

    OUTPUT.chmod(0o600)

    print("\n--- INDEPENDENT AUDIT ---")
    print("Breakouts verified:", 328)
    print("Lifecycle transitions verified:", transition_count)
    print("Candle-close observation timestamps: PASS")
    print("First qualifying reversal timestamps: PASS")
    print("Three-candle expiration timestamps: PASS")
    print("Session-end censoring: PASS")
    print("Past-only EMA, VWAP and RVOL features: PASS")
    print("Protected source files unchanged: PASS")
    print("Untouched out-of-sample test: NO")
    print("Independent source-price verification: NO")
    print("Predictive trading edge established: NO")
    print("Saved: journal/market_replay_timeline_audit_v001.json")

    print("\nREPLAY TIMELINE AUDIT PASSED")
    print("WEBULL REQUESTS: NONE")
    print("AI REQUESTS: NONE")
    print("ORDER EXECUTION: DISABLED")


if __name__ == "__main__":
    main()
