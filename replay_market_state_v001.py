"""JahvaScript point-in-time historical market replay.

Processes candles sequentially. Outcomes are assigned only
when later candles arrive. Research only; no APIs or orders.
"""

import hashlib
import json
import os
from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone, time
from decimal import Decimal
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parent
JOURNAL = ROOT / "journal"
DATASET = JOURNAL / "datasets/M5_SPY_QQQ_v001.json"
RESEARCH = JOURNAL / "expanded_research_v001.json"
OUTPUT = JOURNAL / "market_replay_v001.json"

ET = ZoneInfo("America/New_York")
STEP = timedelta(minutes=5)
LOOKBACK = 12
HORIZON = 3
REFERENCE_DAYS = 5
RVOL_THRESHOLD = Decimal("1.2")

EXPECTED_HASH = (
    "4c55e6c7961e9210f73f1468b20498fe20db4941cd27b10bd11753336292e3cb"
)


def require(ok, message):
    if not ok:
        raise ValueError("REPLAY STOPPED: " + message)


class StreamingEMA:
    def __init__(self, period):
        self.period = period
        self.count = 0
        self.seed_sum = Decimal(0)
        self.value = None
        self.alpha = Decimal(2) / Decimal(period + 1)

    def update(self, close):
        self.count += 1

        if self.count <= self.period:
            self.seed_sum += close

            if self.count == self.period:
                self.value = self.seed_sum / self.period
        else:
            self.value += self.alpha * (
                close - self.value
            )

        return self.value


def parse_bar(bar):
    dt = datetime.fromisoformat(bar["time"])
    require(
        dt.tzinfo is not None and dt.utcoffset() is not None,
        "Missing timezone"
    )

    dt = dt.astimezone(timezone.utc)
    local = dt.astimezone(ET)

    fields = {
        key: Decimal(str(bar[key]))
        for key in ("open", "high", "low", "close", "volume")
    }

    o, h, l, c, v = (
        fields[key]
        for key in ("open", "high", "low", "close", "volume")
    )

    require(
        bar.get("session") == "RTH"
        and local.weekday() < 5
        and time(9, 30) <= local.time() < time(16, 0)
        and local.minute % 5 == 0
        and local.second == 0
        and local.microsecond == 0
        and all(x.is_finite() for x in fields.values())
        and min(o, h, l, c) > 0
        and l <= min(o, c)
        and h >= max(o, c)
        and v >= 0,
        "Invalid historical M5 bar"
    )

    return {
        "dt": dt,
        "day": local.date().isoformat(),
        "slot": local.strftime("%H:%M"),
        **fields
    }


def volume_feature(current, baseline):
    average = sum(baseline, Decimal(0)) / len(baseline)

    if average <= 0:
        return None, "UNAVAILABLE"

    value = current / average

    return (
        value,
        "HIGH" if value >= RVOL_THRESHOLD
        else "LOW_NORMAL"
    )


def replay_symbol(symbol, raw_bars, full_dates):
    bars = [parse_bar(bar) for bar in raw_bars]
    times = [bar["dt"] for bar in bars]

    require(
        len(bars) == 1400
        and times == sorted(times)
        and len(times) == len(set(times)),
        symbol + ": invalid source timeline"
    )

    by_day = defaultdict(list)

    for bar in bars:
        by_day[bar["day"]].append(bar)

    emas = {
        period: StreamingEMA(period)
        for period in (9, 21, 55)
    }

    completed_sessions = {}
    seen_days = []
    events = []
    transitions = []

    full_set = set(full_dates)
    earliest_full = min(full_dates)
    latest_full = max(full_dates)

    for day in sorted(by_day):
        # The initial partial session may seed EMAs.
        # The trailing partial session is outside replay scope.
        if day > latest_full:
            continue

        session = by_day[day]
        analyze = day in full_set

        if analyze:
            require(
                len(session) == 78
                and session[0]["slot"] == "09:30"
                and session[-1]["slot"] == "15:55"
                and all(
                    b["dt"] - a["dt"] == STEP
                    for a, b in zip(session, session[1:])
                ),
                symbol + ": incomplete replay session"
            )
        else:
            require(
                day < earliest_full,
                symbol + ": unexpected incomplete middle session"
            )

        session_seen = []
        open_events = []
        pv = Decimal(0)
        total_volume = Decimal(0)

        for i, bar in enumerate(session):
            close_time = bar["dt"] + STEP

            # Update streaming indicators using this newly
            # completed candle, never subsequent candles.
            for period, accumulator in emas.items():
                bar[f"ema{period}"] = accumulator.update(
                    bar["close"]
                )

            typical = (
                bar["high"] + bar["low"] + bar["close"]
            ) / Decimal(3)

            pv += typical * bar["volume"]
            total_volume += bar["volume"]

            vwap = (
                pv / total_volume
                if total_volume > 0 else None
            )

            # Process already-observed breakouts first.
            remaining = []

            for event in open_events:
                elapsed = i - event["index"]
                require(
                    1 <= elapsed <= HORIZON,
                    "Invalid event lifecycle"
                )

                broken_level = Decimal(event["broken_level"])
                failed = (
                    bar["close"] < broken_level
                    if event["features"]["direction"] == "UP"
                    else bar["close"] > broken_level
                )

                if failed:
                    event["outcome"] = "CONFIRMED_REVERSAL"
                    event["outcome_known_at_utc"] = (
                        close_time.isoformat()
                    )
                    event["confirmation_candle_start_utc"] = (
                        bar["dt"].isoformat()
                    )

                    transitions.append({
                        "id": event["id"],
                        "at_utc": close_time.isoformat(),
                        "state": "CONFIRMED_REVERSAL"
                    })

                elif elapsed == HORIZON:
                    event["outcome"] = (
                        "NO_FAILURE_WITHIN_HORIZON"
                    )
                    event["outcome_known_at_utc"] = (
                        close_time.isoformat()
                    )

                    transitions.append({
                        "id": event["id"],
                        "at_utc": close_time.isoformat(),
                        "state": "NO_FAILURE_WITHIN_HORIZON"
                    })

                else:
                    remaining.append(event)

            open_events = remaining

            if analyze and len(session_seen) >= LOOKBACK:
                prior = session_seen[-LOOKBACK:]
                ceiling = max(x["high"] for x in prior)
                floor = min(x["low"] for x in prior)
                previous_close = session_seen[-1]["close"]

                up = (
                    previous_close <= ceiling < bar["close"]
                )
                down = (
                    previous_close >= floor > bar["close"]
                )

                if up or down:
                    direction = "UP" if up else "DOWN"

                    a, b, c = (
                        bar["ema9"],
                        bar["ema21"],
                        bar["ema55"]
                    )

                    ema_state = (
                        "UNAVAILABLE"
                        if any(x is None for x in (a, b, c))
                        else "BULLISH_STACK"
                        if a > b > c
                        else "BEARISH_STACK"
                        if a < b < c
                        else "MIXED"
                    )

                    vwap_state = (
                        "UNAVAILABLE" if vwap is None
                        else "ABOVE" if bar["close"] > vwap
                        else "BELOW" if bar["close"] < vwap
                        else "AT"
                    )

                    rolling, rolling_group = volume_feature(
                        bar["volume"],
                        [
                            x["volume"]
                            for x in session_seen[-5:]
                        ]
                    )

                    # Only sessions already completed in the
                    # replay may become clock-time references.
                    prior_dates = (
                        seen_days[-REFERENCE_DAYS:]
                        if len(seen_days) >= REFERENCE_DAYS
                        else []
                    )

                    clock = None
                    clock_group = "UNAVAILABLE"

                    if prior_dates:
                        reference_volumes = [
                            completed_sessions[d][bar["slot"]]
                            for d in prior_dates
                        ]

                        clock, clock_group = volume_feature(
                            bar["volume"],
                            reference_volumes
                        )

                    event_id = (
                        symbol + ":" + bar["dt"].isoformat()
                    )

                    require(
                        not any(e["id"] == event_id for e in events),
                        "Duplicate replay event"
                    )

                    event = {
                        "id": event_id,
                        "symbol": symbol,
                        "session_date": day,
                        "index": i,
                        "breakout_candle_start_utc":
                            bar["dt"].isoformat(),
                        "observed_at_utc":
                            close_time.isoformat(),
                        "broken_level": str(
                            ceiling if up else floor
                        ),
                        "features": {
                            "direction": direction,
                            "close": str(bar["close"]),
                            "support": str(floor),
                            "resistance": str(ceiling),
                            "ema9": (
                                str(a) if a is not None else None
                            ),
                            "ema21": (
                                str(b) if b is not None else None
                            ),
                            "ema55": (
                                str(c) if c is not None else None
                            ),
                            "ema_alignment": ema_state,
                            "session_vwap": (
                                str(vwap) if vwap is not None
                                else None
                            ),
                            "vwap_position": vwap_state,
                            "rolling_rvol": (
                                str(rolling)
                                if rolling is not None else None
                            ),
                            "clock_rvol": (
                                str(clock)
                                if clock is not None else None
                            ),
                            "rvol_agreement": (
                                rolling_group + "_TO_"
                                + clock_group
                            ),
                            "clock_reference_dates": prior_dates
                        },
                        "outcome": "OBSERVATION_PENDING",
                        "outcome_known_at_utc": None,
                        "confirmation_candle_start_utc": None,
                        "trade_signal": False
                    }

                    events.append(event)
                    open_events.append(event)

                    transitions.append({
                        "id": event_id,
                        "at_utc": close_time.isoformat(),
                        "state": "BREAKOUT_OBSERVED"
                    })

            session_seen.append(bar)

        if analyze:
            session_close = session[-1]["dt"] + STEP

            # Unresolved events cannot carry their
            # three-candle RTH horizon into another day.
            for event in open_events:
                event["outcome"] = "CENSORED_SESSION_END"
                event["outcome_known_at_utc"] = (
                    session_close.isoformat()
                )

                transitions.append({
                    "id": event["id"],
                    "at_utc": session_close.isoformat(),
                    "state": "CENSORED_SESSION_END"
                })

            completed_sessions[day] = {
                row["slot"]: row["volume"]
                for row in session
            }

            seen_days.append(day)

    require(
        seen_days == full_dates,
        symbol + ": missing or unordered replay days"
    )

    for event in events:
        observed = datetime.fromisoformat(
            event["observed_at_utc"]
        )
        resolved = datetime.fromisoformat(
            event["outcome_known_at_utc"]
        )

        require(
            resolved >= observed
            and event["trade_signal"] is False,
            "Invalid event timestamps or permissions"
        )

        if event["outcome"] != "CENSORED_SESSION_END":
            require(
                resolved > observed,
                "Outcome known before later candle"
            )

    return events, transitions


def main():
    print("\n=== JAHVASCRIPT POINT-IN-TIME MARKET REPLAY ===")

    require(
        "journal/" in (ROOT / ".gitignore").read_text().splitlines(),
        "Journal must be gitignored"
    )
    require(
        not OUTPUT.exists(),
        "Replay V001 exists; refusing overwrite"
    )

    raw = DATASET.read_bytes()
    fingerprint = hashlib.sha256(raw).hexdigest()

    require(
        fingerprint == EXPECTED_HASH,
        "Dataset V001 fingerprint mismatch"
    )

    dataset = json.loads(raw)

    require(
        dataset.get("historical_research_only") is True
        and dataset.get("execution_enabled") is False
        and dataset.get("trade_authorization") is False
        and dataset.get("untouched_out_of_sample_test") is False,
        "Unsafe or unexpected dataset"
    )

    full_dates = sorted(
        dataset["full_standard_session_dates"]
    )
    require(len(full_dates) == 17, "Unexpected session count")

    report = {
        "dataset": "M5_SPY_QQQ_v001",
        "dataset_sha256": fingerprint,
        "generated_utc": datetime.now(
            timezone.utc
        ).isoformat(),
        "complete_sessions_replayed": len(full_dates),
        "bar_observations_available_at_candle_close": True,
        "breakouts_selected_without_future_outcomes": True,
        "prior_complete_sessions_for_clock_rvol": 5,
        "confirmation_horizon_candles": HORIZON,
        "replay_is_historical_not_live": True,
        "out_of_sample": False,
        "source_prices_verified": False,
        "trade_signal": False,
        "execution_enabled": False,
        "trade_authorization": False,
        "symbols": {}
    }

    print("Historical complete sessions:", len(full_dates))
    print("Processing: candle-by-candle")
    print("Candle features available: AT CANDLE CLOSE")
    print("Trade authorization: DISABLED")

    all_events = []

    for symbol in ("SPY", "QQQ"):
        events, transitions = replay_symbol(
            symbol,
            dataset["symbols"][symbol]["bars"],
            full_dates
        )

        counts = Counter(e["outcome"] for e in events)

        report["symbols"][symbol] = {
            "events": events,
            "transitions": transitions,
            "outcome_counts": dict(counts)
        }

        all_events.extend(events)

        print(f"\n--- {symbol} ---")
        print("Breakouts observed:", len(events))
        print(
            "Reversals confirmed later:",
            counts["CONFIRMED_REVERSAL"]
        )
        print(
            "Horizon elapsed without reversal:",
            counts["NO_FAILURE_WITHIN_HORIZON"]
        )
        print(
            "Censored at session end:",
            counts["CENSORED_SESSION_END"]
        )

    # Retrospective report used solely for final
    # reconciliation, never for event discovery.
    historical = json.loads(RESEARCH.read_text())

    require(
        historical.get("dataset_sha256") == fingerprint
        and historical.get("out_of_sample") is False
        and historical.get("execution_enabled") is False,
        "Unexpected reference research report"
    )

    for symbol in ("SPY", "QQQ"):
        reference = {
            datetime.fromisoformat(e["breakout_utc"])
            .astimezone(timezone.utc): e
            for e in historical["symbols"][symbol]["events"]
        }

        events = report["symbols"][symbol]["events"]

        require(
            len(reference) == len(events),
            symbol + ": breakout count mismatch"
        )

        for event in events:
            dt = datetime.fromisoformat(
                event["breakout_candle_start_utc"]
            )
            original = reference.get(dt)

            require(
                original is not None
                and original["direction"]
                    == event["features"]["direction"],
                "Replay breakout mismatch"
            )

            expected = (
                "CENSORED_SESSION_END"
                if original["outcome"] == "PENDING"
                else original["outcome"]
            )

            require(
                event["outcome"] == expected
                and event["confirmation_candle_start_utc"]
                    == original["confirmation_utc"],
                "Replay outcome mismatch"
            )

    totals = Counter(e["outcome"] for e in all_events)

    require(
        len(all_events) == 328
        and totals["CONFIRMED_REVERSAL"] == 155
        and totals["NO_FAILURE_WITHIN_HORIZON"] == 155
        and totals["CENSORED_SESSION_END"] == 18,
        "Final replay reconciliation failure"
    )

    os.umask(0o077)

    with OUTPUT.open("x") as file:
        json.dump(report, file, indent=2)
        file.write("\n")

    OUTPUT.chmod(0o600)

    require(
        hashlib.sha256(DATASET.read_bytes()).hexdigest()
        == fingerprint,
        "Source dataset changed"
    )

    print("\n--- REPLAY VERIFICATION ---")
    print("All historical breakouts reproduced:", len(all_events))
    print("Future outcomes used to select events: NO")
    print("EMA processing: CHRONOLOGICAL")
    print("Clock-volume references: PRIOR SESSIONS ONLY")
    print("Confirmation recorded after breakout: PASS")
    print("Unresolved session-end events: CENSORED")
    print("Retrospective outcome reconciliation: PASS")
    print("Original dataset/reports: UNCHANGED")
    print("Out-of-sample predictive test: NOT PERFORMED")
    print("Source prices independently verified: NO")
    print("Saved: journal/market_replay_v001.json")

    print("\nPOINT-IN-TIME MARKET REPLAY PASSED")
    print("WEBULL REQUESTS: NONE")
    print("AI REQUESTS: NONE")
    print("ORDER EXECUTION: DISABLED")


if __name__ == "__main__":
    main()
