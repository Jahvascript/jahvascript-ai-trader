"""JahvaScript cross-provider breakout sensitivity audit.

Compare Webull and saved Yahoo 5-minute candles using the
same point-in-time breakout and future-outcome definitions.
Offline historical research only. No signals or orders.
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
FOLDER = JOURNAL / "price_verification"

DATASET = JOURNAL / "datasets/M5_SPY_QQQ_v001.json"
RESEARCH = JOURNAL / "expanded_research_v001.json"
DISCREPANCY = FOLDER / "price_discrepancy_audit_v001.json"
COMPARISON = FOLDER / "yahoo_comparison_v001.json"
OUTPUT = FOLDER / "breakout_provider_sensitivity_v001.json"

ET = ZoneInfo("America/New_York")
STEP = timedelta(minutes=5)
LOOKBACK = 12
HORIZON = 3
LEVEL_TOLERANCE = Decimal("0.02")

EXPECTED_HASH = (
    "4c55e6c7961e9210f73f1468b20498fe20db4941cd27b10bd11753336292e3cb"
)

REVERSED = "CONFIRMED_REVERSAL"
SURVIVED = "NO_FAILURE_WITHIN_HORIZON"
CENSORED = "CENSORED_SESSION_END"


def require(ok, message):
    if not ok:
        raise ValueError("SENSITIVITY AUDIT STOPPED: " + message)


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def parse_time(text):
    dt = datetime.fromisoformat(text)
    require(
        dt.tzinfo is not None and dt.utcoffset() is not None,
        "Historical timestamp lacks timezone"
    )
    return dt.astimezone(timezone.utc)


def make_bar(dt, values):
    numbers = {
        field: Decimal(str(values[field]))
        for field in (
            "open", "high", "low", "close", "volume"
        )
    }

    o, h, l, c, v = (
        numbers[field]
        for field in (
            "open", "high", "low", "close", "volume"
        )
    )

    require(
        all(value.is_finite() for value in numbers.values())
        and min(o, h, l, c) > 0
        and l <= min(o, c)
        and h >= max(o, c)
        and v >= 0,
        "Invalid provider OHLCV candle at " + dt.isoformat()
    )

    return {
        **numbers,
        "time": dt,
        "day": dt.astimezone(ET).date().isoformat()
    }


def group_sessions(rows, days, symbol, provider):
    by_day = defaultdict(list)

    for row in rows:
        if row["day"] in days:
            by_day[row["day"]].append(row)

    require(
        len(by_day) == len(days),
        provider + " " + symbol + ": missing sessions"
    )

    for day in days:
        candles = by_day[day]
        times = [bar["time"] for bar in candles]

        require(
            len(candles) == 78
            and times == sorted(times)
            and len(set(times)) == 78
            and all(
                b - a == STEP
                for a, b in zip(times, times[1:])
            ),
            provider + " " + symbol
            + ": unexpected intraday timestamp coverage"
        )

        local_first = times[0].astimezone(ET)
        local_last = times[-1].astimezone(ET)

        require(
            local_first.strftime("%H:%M") == "09:30"
            and local_last.strftime("%H:%M") == "15:55",
            provider + " " + symbol
            + ": RTH session boundaries mismatch"
        )

    return by_day


def load_webull(dataset, symbol, days):
    bars = []

    for raw in dataset["symbols"][symbol]["bars"]:
        dt = parse_time(raw["time"])
        if dt.astimezone(ET).date().isoformat() in days:
            bars.append(make_bar(dt, raw))

    return group_sessions(bars, days, symbol, "Webull")


def load_yahoo(path, symbol, days):
    payload = json.loads(path.read_text())
    chart = payload["chart"]

    require(
        chart.get("error") is None
        and len(chart.get("result") or []) == 1,
        symbol + ": Yahoo response unavailable"
    )

    result = chart["result"][0]
    meta = result["meta"]

    require(
        meta.get("symbol", "").upper() == symbol
        and meta.get("exchangeTimezoneName")
            == "America/New_York",
        symbol + ": unexpected Yahoo instrument"
    )

    times = result["timestamp"]
    quotes = result["indicators"]["quote"]

    require(
        len(quotes) == 1,
        symbol + ": unexpected Yahoo quote structure"
    )

    quote = quotes[0]
    fields = ("open", "high", "low", "close", "volume")

    require(
        all(len(quote[field]) == len(times) for field in fields),
        symbol + ": Yahoo OHLCV lengths differ"
    )

    bars = []

    for index, stamp in enumerate(times):
        dt = datetime.fromtimestamp(
            stamp, tz=timezone.utc
        )
        local = dt.astimezone(ET)

        if local.date().isoformat() not in days:
            continue

        if not (
            (9, 30) <= (local.hour, local.minute) < (16, 0)
        ):
            continue

        values = {
            field: quote[field][index]
            for field in fields
        }

        require(
            all(v is not None for v in values.values()),
            symbol + ": missing OHLCV within full session"
        )

        bars.append(make_bar(dt, values))

    return group_sessions(bars, days, symbol, "Yahoo")


def detect_breakouts(sessions, days):
    """Discovery uses only current and previous candles."""
    found = {}

    for day in days:
        bars = sessions[day]

        for index in range(LOOKBACK, len(bars)):
            previous = bars[index-LOOKBACK:index]
            current = bars[index]
            prior_close = bars[index-1]["close"]

            ceiling = max(bar["high"] for bar in previous)
            floor = min(bar["low"] for bar in previous)

            up = (
                prior_close <= ceiling
                and current["close"] > ceiling
            )
            down = (
                prior_close >= floor
                and current["close"] < floor
            )

            require(
                not (up and down),
                "Ambiguous simultaneous breakout"
            )

            if not (up or down):
                continue

            direction = "UP" if up else "DOWN"
            level = ceiling if up else floor

            # Only this outcome calculation reads later bars.
            confirmation = None

            for future in bars[
                index+1:min(index+HORIZON+1, len(bars))
            ]:
                failed = (
                    future["close"] < level
                    if up else future["close"] > level
                )

                if failed:
                    confirmation = future["time"]
                    break

            if confirmation is not None:
                outcome = REVERSED
            elif index + HORIZON < len(bars):
                outcome = SURVIVED
            else:
                outcome = CENSORED

            key = current["time"].isoformat()

            require(
                key not in found,
                "Duplicate breakout timestamp"
            )

            found[key] = {
                "day": day,
                "direction": direction,
                "level": level,
                "outcome": outcome,
                "confirmation": confirmation
            }

    return found


def outcome_counts(events):
    return Counter(
        event["outcome"] for event in events.values()
    )


def verify_webull_against_research(
    symbol, discovered, research
):
    reference = {}

    for event in research["symbols"][symbol]["events"]:
        key = parse_time(
            event["breakout_utc"]
        ).isoformat()

        require(
            key not in reference,
            "Duplicate historical reference event"
        )

        reference[key] = event

    require(
        set(reference) == set(discovered),
        symbol + ": Webull breakout set differs from "
        "the original independently audited research"
    )

    for key, event in discovered.items():
        old = reference[key]

        expected_outcome = (
            CENSORED if old["outcome"] == "PENDING"
            else old["outcome"]
        )

        old_confirmation = (
            parse_time(old["confirmation_utc"])
            if old["confirmation_utc"] is not None
            else None
        )

        require(
            event["direction"] == old["direction"]
            and event["outcome"] == expected_outcome
            and event["confirmation"] == old_confirmation,
            symbol + ": Webull event or outcome mismatch"
        )


def compare_symbol(symbol, webull, yahoo):
    webull_keys = set(webull)
    yahoo_keys = set(yahoo)

    common = sorted(webull_keys & yahoo_keys)
    webull_only = sorted(webull_keys - yahoo_keys)
    yahoo_only = sorted(yahoo_keys - webull_keys)

    same_direction = []
    opposite_direction = []

    for key in common:
        if webull[key]["direction"] == yahoo[key]["direction"]:
            same_direction.append(key)
        else:
            opposite_direction.append(key)

    outcome_changes = [
        key for key in same_direction
        if webull[key]["outcome"] != yahoo[key]["outcome"]
    ]

    confirmation_changes = [
        key for key in same_direction
        if (
            webull[key]["confirmation"]
            != yahoo[key]["confirmation"]
        )
    ]

    level_changes = [
        key for key in same_direction
        if abs(
            webull[key]["level"] - yahoo[key]["level"]
        ) > LEVEL_TOLERANCE
    ]

    dates = defaultdict(
        lambda: {
            "webull": 0,
            "yahoo": 0,
            "webull_only": 0,
            "yahoo_only": 0,
            "common_outcome_changes": 0
        }
    )

    for event in webull.values():
        dates[event["day"]]["webull"] += 1

    for event in yahoo.values():
        dates[event["day"]]["yahoo"] += 1

    for key in webull_only:
        dates[webull[key]["day"]]["webull_only"] += 1

    for key in yahoo_only:
        dates[yahoo[key]["day"]]["yahoo_only"] += 1

    for key in outcome_changes:
        dates[webull[key]["day"]][
            "common_outcome_changes"
        ] += 1

    def describe(key, provider):
        event = provider[key]
        return {
            "time_utc": key,
            "direction": event["direction"],
            "outcome": event["outcome"],
            "level": str(event["level"])
        }

    print("\n---", symbol, "---")
    print("Webull breakout events:", len(webull))
    print("Yahoo breakout events:", len(yahoo))
    print("Common breakout timestamps:", len(common))
    print("Webull-only events:", len(webull_only))
    print("Yahoo-only events:", len(yahoo_only))
    print(
        "Different directions at common timestamps:",
        len(opposite_direction)
    )
    print(
        "Different outcomes at same-direction events:",
        len(outcome_changes)
    )
    print(
        "Different confirmation timestamps:",
        len(confirmation_changes)
    )
    print(
        "Broken levels differing over $0.02:",
        len(level_changes)
    )

    print("\nWEBULL OUTCOMES")
    for name in (REVERSED, SURVIVED, CENSORED):
        print(" ", name + ":", outcome_counts(webull)[name])

    print("\nYAHOO OUTCOMES")
    for name in (REVERSED, SURVIVED, CENSORED):
        print(" ", name + ":", outcome_counts(yahoo)[name])

    print("\nEXAMPLES OF CHANGED DETECTIONS")
    examples = (
        [("WEBULL_ONLY", describe(k, webull))
         for k in webull_only[:3]]
        + [("YAHOO_ONLY", describe(k, yahoo))
           for k in yahoo_only[:3]]
    )

    if not examples:
        print("  None")

    for label, item in examples:
        print(
            " ", label,
            item["time_utc"],
            item["direction"],
            item["outcome"]
        )

    print("\nEXAMPLES OF CHANGED OUTCOMES")
    if not outcome_changes:
        print("  None")

    for key in outcome_changes[:5]:
        print(
            " ", key,
            "Webull:", webull[key]["outcome"],
            "| Yahoo:", yahoo[key]["outcome"]
        )

    return {
        "webull_breakouts": len(webull),
        "yahoo_breakouts": len(yahoo),
        "common_timestamps": len(common),
        "same_direction_common": len(same_direction),
        "opposite_direction_common": len(opposite_direction),
        "webull_only_count": len(webull_only),
        "yahoo_only_count": len(yahoo_only),
        "same_direction_outcome_changes": len(outcome_changes),
        "same_direction_confirmation_changes":
            len(confirmation_changes),
        "same_direction_level_changes_over_2c":
            len(level_changes),
        "webull_outcomes": dict(outcome_counts(webull)),
        "yahoo_outcomes": dict(outcome_counts(yahoo)),
        "by_day": dict(sorted(dates.items())),
        "webull_only_examples": [
            describe(key, webull) for key in webull_only[:12]
        ],
        "yahoo_only_examples": [
            describe(key, yahoo) for key in yahoo_only[:12]
        ],
        "common_outcome_change_examples": [
            {
                "time_utc": key,
                "webull": webull[key]["outcome"],
                "yahoo": yahoo[key]["outcome"]
            }
            for key in outcome_changes[:12]
        ],
        "opposite_direction_examples":
            opposite_direction[:12]
    }


def main():
    print(
        "\n=== JAHVASCRIPT CROSS-PROVIDER BREAKOUT AUDIT ==="
    )

    require(
        "journal/" in (
            ROOT / ".gitignore"
        ).read_text().splitlines(),
        "Journal must be gitignored"
    )

    require(
        not OUTPUT.exists(),
        "V001 audit report already exists; "
        "refusing overwrite"
    )

    raw_paths = {
        symbol: FOLDER / (
            "yahoo_" + symbol.lower() + "_m5_raw_v001.json"
        )
        for symbol in ("SPY", "QQQ")
    }

    protected = [
        DATASET, RESEARCH, DISCREPANCY, COMPARISON,
        *raw_paths.values()
    ]

    before = {
        str(path): sha(path)
        for path in protected
    }

    require(
        before[str(DATASET)] == EXPECTED_HASH,
        "Dataset V001 fingerprint changed"
    )

    dataset = json.loads(DATASET.read_text())
    research = json.loads(RESEARCH.read_text())
    previous = json.loads(COMPARISON.read_text())
    discrepancy = json.loads(DISCREPANCY.read_text())

    require(
        dataset.get("historical_research_only") is True
        and dataset.get("execution_enabled") is False
        and dataset.get("trade_authorization") is False
        and research.get("dataset_sha256") == EXPECTED_HASH
        and research.get("out_of_sample") is False
        and research.get("execution_enabled") is False
        and research.get("trade_authorization") is False
        and previous.get("dataset_sha256") == EXPECTED_HASH
        and previous.get("execution_enabled") is False
        and discrepancy.get("dataset_sha256") == EXPECTED_HASH
        and discrepancy.get("comparison_sha256")
            == before[str(COMPARISON)]
        and discrepancy.get("execution_enabled") is False,
        "Research linkage or safety flags disagree"
    )

    for symbol in ("SPY", "QQQ"):
        require(
            sha(raw_paths[symbol])
            == previous["raw_provider_responses"][
                symbol
            ]["sha256"],
            symbol + ": provider source fingerprint mismatch"
        )

    days = sorted(dataset["full_standard_session_dates"])
    require(len(days) == 17, "Session coverage mismatch")

    print("Dataset: M5_SPY_QQQ_v001")
    print("Complete sessions:", len(days))
    print("Breakout lookback: 12 previous candles")
    print("Confirmation horizon: 3 later candles")
    print("Provider data: EXISTING LOCAL FILES")
    print("New market-data requests: NONE")

    results = {}

    for symbol in ("SPY", "QQQ"):
        a = load_webull(dataset, symbol, days)
        b = load_yahoo(raw_paths[symbol], symbol, days)

        for day in days:
            require(
                [x["time"] for x in a[day]]
                == [x["time"] for x in b[day]],
                symbol + ": unmatched provider candle timestamps"
            )

        webull_events = detect_breakouts(a, days)
        yahoo_events = detect_breakouts(b, days)

        verify_webull_against_research(
            symbol, webull_events, research
        )

        results[symbol] = compare_symbol(
            symbol, webull_events, yahoo_events
        )

    require(
        results["SPY"]["webull_breakouts"] == 173
        and results["QQQ"]["webull_breakouts"] == 155,
        "Audited Webull event counts changed"
    )

    require(
        all(sha(path) == before[str(path)] for path in protected),
        "Protected source changed during comparison"
    )

    report = {
        "version": "BREAKOUT_PROVIDER_SENSITIVITY_V001",
        "generated_utc": datetime.now(
            timezone.utc
        ).isoformat(),
        "dataset_sha256": EXPECTED_HASH,
        "protected_source_sha256": {
            path.relative_to(ROOT).as_posix():
                before[str(path)]
            for path in protected
        },
        "lookback_previous_candles": LOOKBACK,
        "future_confirmation_candles": HORIZON,
        "breakouts_detected_without_future_data": True,
        "outcomes_attached_retrospectively": True,
        "comparisons": results,
        "source_price_accuracy_established": False,
        "vendor_conventions_reconciled": False,
        "out_of_sample_validation": False,
        "predictive_edge_established": False,
        "historical_research_only": True,
        "trade_signal": False,
        "execution_enabled": False,
        "trade_authorization": False
    }

    os.umask(0o077)

    with OUTPUT.open("x") as file:
        json.dump(report, file, indent=2)
        file.write("\n")

    OUTPUT.chmod(0o600)

    require(
        all(sha(path) == before[str(path)] for path in protected),
        "Protected source changed after report creation"
    )

    print("\n--- FINAL SENSITIVITY AUDIT ---")
    print("Webull historical event reproduction: PASS")
    print("Yahoo independent breakout computation: PASS")
    print("Timestamp alignment: PASS")
    print("Provider differences: RECORDED, NOT CORRECTED")
    print("Original research and price data: UNCHANGED")
    print("Provider accuracy established: NO")
    print("Predictive trading edge established: NO")
    print(
        "Saved: journal/price_verification/"
        "breakout_provider_sensitivity_v001.json"
    )

    print("\nCROSS-PROVIDER BREAKOUT AUDIT PASSED")
    print("WEBULL REQUESTS: NONE")
    print("YAHOO REQUESTS: NONE")
    print("AI REQUESTS: NONE")
    print("ORDER EXECUTION: DISABLED")


if __name__ == "__main__":
    main()
