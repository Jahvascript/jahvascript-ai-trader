"""JahvaScript provider threshold sensitivity audit.

Uses frozen Webull and saved Yahoo candles.
Thresholds are exploratory, not optimized trading rules.
No market requests, AI calls, or orders.
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
SENSITIVITY = FOLDER / "breakout_provider_sensitivity_v001.json"
EDGE_CASES = FOLDER / "provider_edge_cases_v001.json"
OUTPUT = FOLDER / "breakout_threshold_sensitivity_v001.json"

YAHOO = {
    symbol: FOLDER / f"yahoo_{symbol.lower()}_m5_raw_v001.json"
    for symbol in ("SPY", "QQQ")
}

EXPECTED_HASH = (
    "4c55e6c7961e9210f73f1468b20498fe20db4941cd27b10bd11753336292e3cb"
)

ET = ZoneInfo("America/New_York")
STEP = timedelta(minutes=5)
LOOKBACK = 12
HORIZON = 3

MARGINS = (
    Decimal("0.00"),
    Decimal("0.01"),
    Decimal("0.02"),
    Decimal("0.05")
)

REVERSED = "CONFIRMED_REVERSAL"
SURVIVED = "NO_FAILURE_WITHIN_HORIZON"
CENSORED = "CENSORED_SESSION_END"

CASES = (
    ("SPY", "2026-10-08T19:55:00+00:00"),
    ("SPY", "2026-10-13T14:30:00+00:00"),
    ("SPY", "2026-10-09T19:50:00+00:00"),
    ("QQQ", "2026-10-07T19:50:00+00:00")
)


def require(ok, message):
    if not ok:
        raise ValueError("THRESHOLD AUDIT STOPPED: " + message)


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def parse(value):
    dt = datetime.fromisoformat(value)
    require(
        dt.tzinfo is not None and dt.utcoffset() is not None,
        "Missing timestamp timezone"
    )
    return dt.astimezone(timezone.utc)


def record(dt, values):
    numbers = {
        name: Decimal(str(values[name]))
        for name in ("open", "high", "low", "close", "volume")
    }

    o = numbers["open"]
    h = numbers["high"]
    l = numbers["low"]
    c = numbers["close"]
    v = numbers["volume"]

    require(
        all(x.is_finite() for x in numbers.values())
        and min(o, h, l, c) > 0
        and h >= max(o, c)
        and l <= min(o, c)
        and v >= 0,
        "Invalid OHLCV at " + dt.isoformat()
    )

    return {
        **numbers,
        "time": dt,
        "day": dt.astimezone(ET).date().isoformat()
    }


def group(rows, dates):
    sessions = defaultdict(list)

    for row in rows:
        if row["day"] in dates:
            sessions[row["day"]].append(row)

    require(
        set(sessions) == set(dates),
        "Provider trading-date coverage mismatch"
    )

    for day in dates:
        candles = sessions[day]
        times = [x["time"] for x in candles]

        require(
            len(candles) == 78
            and times == sorted(times)
            and len(set(times)) == 78
            and all(
                b - a == STEP
                for a, b in zip(times, times[1:])
            )
            and times[0].astimezone(ET).strftime("%H:%M")
                == "09:30"
            and times[-1].astimezone(ET).strftime("%H:%M")
                == "15:55",
            "Invalid complete-session candle timeline"
        )

    return sessions


def webull_sessions(dataset, symbol, dates):
    rows = []

    for bar in dataset["symbols"][symbol]["bars"]:
        dt = parse(bar["time"])
        if dt.astimezone(ET).date().isoformat() in dates:
            rows.append(record(dt, bar))

    return group(rows, dates)


def yahoo_sessions(path, symbol, dates):
    payload = json.loads(path.read_text())
    chart = payload["chart"]

    require(
        chart.get("error") is None
        and len(chart.get("result") or []) == 1,
        "Yahoo provider result missing"
    )

    result = chart["result"][0]

    require(
        result["meta"]["symbol"].upper() == symbol
        and result["meta"]["exchangeTimezoneName"]
            == "America/New_York",
        "Yahoo instrument/timezone mismatch"
    )

    stamps = result["timestamp"]
    quote = result["indicators"]["quote"][0]
    fields = ("open", "high", "low", "close", "volume")

    require(
        all(len(quote[field]) == len(stamps) for field in fields),
        "Yahoo quote array lengths mismatch"
    )

    rows = []

    for i, stamp in enumerate(stamps):
        dt = datetime.fromtimestamp(stamp, timezone.utc)
        local = dt.astimezone(ET)

        if local.date().isoformat() not in dates:
            continue

        if not (
            (9, 30) <= (local.hour, local.minute) < (16, 0)
        ):
            continue

        values = {field: quote[field][i] for field in fields}

        require(
            all(value is not None for value in values.values()),
            "Yahoo complete-session candle missing OHLCV"
        )

        rows.append(record(dt, values))

    return group(rows, dates)


def detect(sessions, dates, margin):
    events = {}

    for day in dates:
        candles = sessions[day]

        for index in range(LOOKBACK, len(candles)):
            previous = candles[index-LOOKBACK:index]
            current = candles[index]

            resistance = max(x["high"] for x in previous)
            support = min(x["low"] for x in previous)
            previous_close = candles[index-1]["close"]
            close = current["close"]

            up = (
                previous_close <= resistance
                and close > resistance + margin
            )

            down = (
                previous_close >= support
                and close < support - margin
            )

            require(
                not (up and down),
                "Ambiguous breakout direction"
            )

            if not (up or down):
                continue

            direction = "UP" if up else "DOWN"
            level = resistance if up else support

            subsequent = candles[
                index+1:min(index+HORIZON+1, len(candles))
            ]

            failed_at = None

            for future in subsequent:
                reversal = (
                    future["close"] < level - margin
                    if up else future["close"] > level + margin
                )

                if reversal:
                    failed_at = future["time"].isoformat()
                    break

            if failed_at:
                outcome = REVERSED
            elif len(subsequent) == HORIZON:
                outcome = SURVIVED
            else:
                outcome = CENSORED

            key = current["time"].isoformat()

            require(
                key not in events,
                "Duplicate breakout timestamp"
            )

            events[key] = {
                "direction": direction,
                "outcome": outcome,
                "confirmation": failed_at,
                "level": str(level),
                "full_horizon_available":
                    len(subsequent) == HORIZON,
                "early_confirmed_in_short_window":
                    outcome == REVERSED
                    and len(subsequent) < HORIZON
            }

    return events


def summarize(webull, yahoo):
    wa = set(webull)
    ya = set(yahoo)

    shared = sorted(wa & ya)
    same_direction = [
        key for key in shared
        if webull[key]["direction"] == yahoo[key]["direction"]
    ]

    changed_outcomes = [
        key for key in same_direction
        if webull[key]["outcome"] != yahoo[key]["outcome"]
    ]

    def compact(events):
        outcomes = Counter(
            row["outcome"] for row in events.values()
        )

        return {
            "events": len(events),
            "confirmed": outcomes[REVERSED],
            "no_failure": outcomes[SURVIVED],
            "censored": outcomes[CENSORED],
            "early_confirmations_without_full_horizon": sum(
                row["early_confirmed_in_short_window"]
                for row in events.values()
            )
        }

    return {
        "webull": compact(webull),
        "yahoo": compact(yahoo),
        "common_timestamps": len(shared),
        "same_direction_common": len(same_direction),
        "opposite_direction_common":
            len(shared) - len(same_direction),
        "webull_only": len(wa - ya),
        "yahoo_only": len(ya - wa),
        "different_outcomes_on_common": len(changed_outcomes),
        "union_events": len(wa | ya),
        "event_overlap_percent": round(
            100 * len(shared) / len(wa | ya), 2
        ) if wa | ya else None,
        "changed_outcome_examples": changed_outcomes[:8]
    }


def main():
    print(
        "\n=== JAHVASCRIPT BREAKOUT THRESHOLD AUDIT ==="
    )

    require(
        "journal/" in (
            ROOT / ".gitignore"
        ).read_text().splitlines(),
        "Research journal must be gitignored"
    )

    require(
        not OUTPUT.exists(),
        "V001 threshold audit already exists"
    )

    protected = [
        DATASET, SENSITIVITY, EDGE_CASES,
        *YAHOO.values()
    ]

    before = {
        str(path): sha(path) for path in protected
    }

    require(
        before[str(DATASET)] == EXPECTED_HASH,
        "Dataset fingerprint mismatch"
    )

    dataset = json.loads(DATASET.read_text())
    previous = json.loads(SENSITIVITY.read_text())
    cases_report = json.loads(EDGE_CASES.read_text())

    require(
        dataset.get("historical_research_only") is True
        and dataset.get("execution_enabled") is False
        and dataset.get("trade_authorization") is False
        and previous.get("dataset_sha256") == EXPECTED_HASH
        and previous.get("execution_enabled") is False
        and previous.get("trade_authorization") is False
        and cases_report.get("dataset_sha256") == EXPECTED_HASH
        and cases_report.get("sensitivity_report_sha256")
            == before[str(SENSITIVITY)]
        and cases_report.get("cases_examined") == 4
        and cases_report.get("execution_enabled") is False,
        "Source audit linkage/safety mismatch"
    )

    dates = sorted(dataset["full_standard_session_dates"])
    require(len(dates) == 17, "Session coverage mismatch")

    sessions = {}
    for symbol in ("SPY", "QQQ"):
        sessions[symbol] = {
            "Webull": webull_sessions(
                dataset, symbol, dates
            ),
            "Yahoo": yahoo_sessions(
                YAHOO[symbol], symbol, dates
            )
        }

        for day in dates:
            require(
                [x["time"] for x in sessions[symbol]["Webull"][day]]
                == [x["time"] for x in sessions[symbol]["Yahoo"][day]],
                "Provider timestamps not aligned"
            )

    results = {}
    case_summaries = {}

    print("Source: 17 complete sessions")
    print("Lookback: 12 earlier candles")
    print("Horizon: 3 later candles")
    print("Margins: $0.00, $0.01, $0.02, $0.05")
    print("No new provider requests")

    for margin in MARGINS:
        key = str(margin)
        results[key] = {}
        detected = {}

        print("\n--- MINIMUM BREAKOUT/REVERSAL MARGIN $"
              + key + " ---")

        for symbol in ("SPY", "QQQ"):
            webull = detect(
                sessions[symbol]["Webull"],
                dates, margin
            )
            yahoo = detect(
                sessions[symbol]["Yahoo"],
                dates, margin
            )

            detected[symbol] = {
                "Webull": webull,
                "Yahoo": yahoo
            }

            stats = summarize(webull, yahoo)
            results[key][symbol] = stats

            # Strict zero-margin behavior must exactly
            # reproduce the prior independently audited
            # cross-provider event counts.
            if margin == Decimal("0.00"):
                old = previous["comparisons"][symbol]

                require(
                    stats["webull"]["events"]
                        == old["webull_breakouts"]
                    and stats["yahoo"]["events"]
                        == old["yahoo_breakouts"]
                    and stats["common_timestamps"]
                        == old["common_timestamps"]
                    and stats["webull_only"]
                        == old["webull_only_count"]
                    and stats["yahoo_only"]
                        == old["yahoo_only_count"]
                    and stats["different_outcomes_on_common"]
                        == old["same_direction_outcome_changes"],
                    symbol + ": zero-margin baseline mismatch"
                )

            print(
                symbol,
                "| Webull:", stats["webull"]["events"],
                "| Yahoo:", stats["yahoo"]["events"],
                "| matched:", stats["common_timestamps"],
                "| overlap:", str(stats["event_overlap_percent"]) + "%",
                "| outcome disagreements:",
                stats["different_outcomes_on_common"]
            )

            print(
                "  Webull outcomes:",
                stats["webull"]["confirmed"],
                "reversed;",
                stats["webull"]["censored"],
                "censored"
            )
            print(
                "  Yahoo outcomes:",
                stats["yahoo"]["confirmed"],
                "reversed;",
                stats["yahoo"]["censored"],
                "censored"
            )

        case_summaries[key] = []

        for symbol, timestamp in CASES:
            canonical_time = parse(timestamp).isoformat()
            row = {
                "symbol": symbol,
                "timestamp_utc": canonical_time
            }

            for provider in ("Webull", "Yahoo"):
                event = detected[symbol][provider].get(
                    canonical_time
                )
                row[provider.lower()] = (
                    event["outcome"]
                    if event is not None
                    else "NOT_A_BREAKOUT"
                )

            case_summaries[key].append(row)

    print("\n--- FOUR DISPUTED EVENTS BY MARGIN ---")
    for margin, rows in case_summaries.items():
        print("Margin $" + margin)

        for item in rows:
            print(
                " ", item["symbol"],
                item["timestamp_utc"],
                "| W:", item["webull"],
                "| Y:", item["yahoo"]
            )

    require(
        all(sha(path) == before[str(path)] for path in protected),
        "Protected source changed during audit"
    )

    report = {
        "version": "BREAKOUT_THRESHOLD_SENSITIVITY_V001",
        "generated_utc": datetime.now(
            timezone.utc
        ).isoformat(),
        "dataset_sha256": EXPECTED_HASH,
        "source_hashes": {
            path.relative_to(ROOT).as_posix(): before[str(path)]
            for path in protected
        },
        "margins_usd": [str(x) for x in MARGINS],
        "definitions": {
            "up_breakout":
                "previous_close <= prior_resistance AND "
                "current_close > resistance + margin",
            "down_breakout":
                "previous_close >= prior_support AND "
                "current_close < support - margin",
            "up_reversal":
                "subsequent_close < resistance - margin",
            "down_reversal":
                "subsequent_close > support + margin",
            "horizon": "Next 3 same-session candles",
            "censoring": "No confirmation with fewer than 3 "
                         "available future candles"
        },
        "results": results,
        "disputed_cases": case_summaries,
        "margins_predefined_for_sensitivity_only": True,
        "recommended_margin": None,
        "statistical_edge_established": False,
        "price_source_accuracy_established": False,
        "out_of_sample_validation": False,
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
        "Source changed after report generation"
    )

    print("\nZero-margin historical baseline: VERIFIED")
    print("Four fixed sensitivity margins: EVALUATED")
    print("Existing research and prices: UNCHANGED")
    print("Threshold optimization: NOT PERFORMED")
    print("Source accuracy established: NO")
    print("Predictive trading advantage established: NO")
    print("Saved: journal/price_verification/"
          "breakout_threshold_sensitivity_v001.json")

    print("\nBREAKOUT THRESHOLD AUDIT PASSED")
    print("WEBULL REQUESTS: NONE")
    print("YAHOO REQUESTS: NONE")
    print("AI REQUESTS: NONE")
    print("ORDER EXECUTION: DISABLED")


if __name__ == "__main__":
    main()
