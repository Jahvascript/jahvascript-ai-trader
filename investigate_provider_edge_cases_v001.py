"""JahvaScript provider disagreement investigation.

Read-only comparison of four disputed breakout observations.
Uses frozen Webull Dataset V001 and saved Yahoo responses.
No network access, AI requests, trade signals or orders.
"""

import hashlib
import json
import os
from collections import defaultdict
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parent
JOURNAL = ROOT / "journal"
FOLDER = JOURNAL / "price_verification"

DATASET = JOURNAL / "datasets/M5_SPY_QQQ_v001.json"
COMPARISON = FOLDER / "yahoo_comparison_v001.json"
SENSITIVITY = FOLDER / "breakout_provider_sensitivity_v001.json"
OUTPUT = FOLDER / "provider_edge_cases_v001.json"

EXPECTED_HASH = (
    "4c55e6c7961e9210f73f1468b20498fe20db4941cd27b10bd11753336292e3cb"
)

ET = ZoneInfo("America/New_York")
FIELDS = ("open", "high", "low", "close", "volume")
LOOKBACK = 12
HORIZON = 3

CASES = (
    ("SPY", "2026-10-08T19:55:00+00:00"),
    ("SPY", "2026-10-13T14:30:00+00:00"),
    ("SPY", "2026-10-09T19:50:00+00:00"),
    ("QQQ", "2026-10-07T19:50:00+00:00")
)


def require(ok, message):
    if not ok:
        raise ValueError("EDGE-CASE AUDIT STOPPED: " + message)


def fingerprint(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def parse(value):
    dt = datetime.fromisoformat(value)
    require(
        dt.tzinfo is not None and dt.utcoffset() is not None,
        "Missing timezone"
    )
    return dt.astimezone(timezone.utc)


def make_row(dt, values):
    data = {
        field: Decimal(str(values[field]))
        for field in FIELDS
    }

    require(
        all(v.is_finite() for v in data.values())
        and min(data[f] for f in FIELDS[:4]) > 0
        and data["volume"] >= 0,
        "Invalid OHLCV"
    )

    data["utc"] = dt.isoformat()
    data["day"] = dt.astimezone(ET).date().isoformat()
    return data


def webull_sessions(dataset, symbol, dates):
    sessions = defaultdict(list)

    for raw in dataset["symbols"][symbol]["bars"]:
        dt = parse(raw["time"])
        row = make_row(dt, raw)

        if row["day"] in dates:
            sessions[row["day"]].append(row)

    return sessions


def yahoo_sessions(path, symbol, dates):
    payload = json.loads(path.read_text())
    chart = payload["chart"]

    require(
        chart.get("error") is None
        and len(chart["result"]) == 1,
        "Yahoo response unavailable"
    )

    result = chart["result"][0]

    require(
        result["meta"]["symbol"].upper() == symbol,
        "Yahoo instrument mismatch"
    )

    timestamps = result["timestamp"]
    quote = result["indicators"]["quote"][0]

    sessions = defaultdict(list)

    for i, stamp in enumerate(timestamps):
        dt = datetime.fromtimestamp(
            stamp, timezone.utc
        )
        local = dt.astimezone(ET)
        day = local.date().isoformat()

        if day not in dates:
            continue

        if not (
            (9, 30) <= (local.hour, local.minute)
            < (16, 0)
        ):
            continue

        values = {
            field: quote[field][i]
            for field in FIELDS
        }

        require(
            all(value is not None for value in values.values()),
            "Missing Yahoo OHLCV on complete session"
        )

        sessions[day].append(
            make_row(dt, values)
        )

    return sessions


def validate_sessions(sessions, dates, label):
    require(
        set(sessions) == set(dates),
        label + ": missing or unexpected trading dates"
    )

    for day in dates:
        rows = sessions[day]

        require(
            len(rows) == 78
            and rows[0]["utc"] < rows[-1]["utc"]
            and len({
                row["utc"] for row in rows
            }) == 78,
            label + ": incomplete session"
        )


def classify_at(rows, index):
    require(
        LOOKBACK <= index < len(rows),
        "Invalid breakout index"
    )

    previous = rows[index-LOOKBACK:index]
    current = rows[index]

    resistance = max(
        row["high"] for row in previous
    )
    support = min(
        row["low"] for row in previous
    )
    last_close = rows[index-1]["close"]
    current_close = current["close"]

    upward = (
        last_close <= resistance
        and current_close > resistance
    )
    downward = (
        last_close >= support
        and current_close < support
    )

    require(
        not (upward and downward),
        "Ambiguous breakout"
    )

    direction = (
        "UP" if upward
        else "DOWN" if downward
        else None
    )

    level = (
        resistance if direction == "UP"
        else support if direction == "DOWN"
        else None
    )

    future = rows[
        index+1:min(index+HORIZON+1, len(rows))
    ]

    confirmation = None

    if direction is not None:
        for bar in future:
            reversed_breakout = (
                bar["close"] < level
                if direction == "UP"
                else bar["close"] > level
            )

            if reversed_breakout:
                confirmation = bar["utc"]
                break

    if direction is None:
        outcome = "NOT_A_BREAKOUT"
    elif confirmation is not None:
        outcome = "CONFIRMED_REVERSAL"
    elif len(future) == HORIZON:
        outcome = "NO_FAILURE_WITHIN_HORIZON"
    else:
        outcome = "CENSORED_SESSION_END"

    return {
        "direction": direction,
        "outcome": outcome,
        "confirmation_utc": confirmation,
        "support": support,
        "resistance": resistance,
        "broken_level": level,
        "previous_close": last_close,
        "current_close": current_close,
        "available_future_candles": len(future),
        "future": future
    }


def as_json(result):
    fields = (
        "direction", "outcome", "confirmation_utc",
        "available_future_candles"
    )

    obj = {
        name: result[name]
        for name in fields
    }

    for name in (
        "support", "resistance", "broken_level",
        "previous_close", "current_close"
    ):
        value = result[name]
        obj[name] = str(value) if value is not None else None

    obj["future_closes"] = [
        {
            "utc": row["utc"],
            "close": str(row["close"])
        }
        for row in result["future"]
    ]

    return obj


def show_case(symbol, utc_time, webull, yahoo):
    dt = parse(utc_time)
    day = dt.astimezone(ET).date().isoformat()

    wb = webull[day]
    yh = yahoo[day]

    indices_a = [
        i for i, row in enumerate(wb)
        if row["utc"] == dt.isoformat()
    ]
    indices_b = [
        i for i, row in enumerate(yh)
        if row["utc"] == dt.isoformat()
    ]

    require(
        len(indices_a) == len(indices_b) == 1,
        "Disputed timestamp not uniquely located"
    )

    ia, ib = indices_a[0], indices_b[0]
    require(
        ia == ib,
        "Provider candle positions differ"
    )

    a = classify_at(wb, ia)
    b = classify_at(yh, ib)

    print(
        "\n---", symbol,
        dt.astimezone(ET).strftime(
            "%Y-%m-%d %I:%M %p ET"
        ),
        "---"
    )
    print(
        "Session candle:",
        ia + 1, "/ 78"
    )

    for name, result in (
        ("WEBULL", a),
        ("YAHOO", b)
    ):
        print("\n", name)
        print(
            "  Direction:",
            result["direction"] or "NO BREAKOUT"
        )
        print(
            "  Previous close:",
            result["previous_close"]
        )
        print(
            "  Current close:",
            result["current_close"]
        )
        print(
            "  Resistance:",
            result["resistance"]
        )
        print(
            "  Support:",
            result["support"]
        )

        if result["direction"]:
            margin = (
                result["current_close"]
                - result["broken_level"]
            )
            print(
                "  Signed breakout margin:",
                margin
            )

        print(
            "  Outcome:",
            result["outcome"]
        )
        print(
            "  Future candles available:",
            result["available_future_candles"],
            "/ 3"
        )

        for future in result["future"]:
            if result["broken_level"] is None:
                distance = "N/A"
            else:
                distance = str(
                    future["close"]
                    - result["broken_level"]
                )

            print(
                "  Future close:",
                future["utc"],
                "=",
                future["close"],
                "| close minus broken level:",
                distance
            )

    differences = {
        field: abs(
            wb[ia][field] - yh[ib][field]
        )
        for field in FIELDS[:4]
    }

    print("\nCURRENT-CANDLE PRICE DIFFERENCES")
    for field, value in differences.items():
        print(" ", field.upper() + ":", value)

    return {
        "symbol": symbol,
        "utc": dt.isoformat(),
        "local": dt.astimezone(ET).isoformat(),
        "candle_index": ia,
        "webull": as_json(a),
        "yahoo": as_json(b),
        "current_ohlc_differences": {
            key: str(value)
            for key, value in differences.items()
        }
    }


def scan_events(sessions, dates):
    found = {}

    for day in dates:
        bars = sessions[day]

        for index in range(LOOKBACK, len(bars)):
            event = classify_at(bars, index)

            if event["direction"] is not None:
                found[bars[index]["utc"]] = event

    return found


def main():
    print(
        "\n=== JAHVASCRIPT PROVIDER EDGE-CASE AUDIT ==="
    )

    require(
        "journal/" in (
            ROOT / ".gitignore"
        ).read_text().splitlines(),
        "Research journal must be gitignored"
    )

    require(
        not OUTPUT.exists(),
        "Edge-case V001 report already exists"
    )

    paths = [
        DATASET,
        COMPARISON,
        SENSITIVITY,
        FOLDER / "yahoo_spy_m5_raw_v001.json",
        FOLDER / "yahoo_qqq_m5_raw_v001.json"
    ]

    before = {
        str(path): fingerprint(path)
        for path in paths
    }

    require(
        before[str(DATASET)] == EXPECTED_HASH,
        "Historical dataset fingerprint mismatch"
    )

    dataset = json.loads(DATASET.read_text())
    comparison = json.loads(COMPARISON.read_text())
    sensitivity = json.loads(SENSITIVITY.read_text())

    require(
        dataset.get("execution_enabled") is False
        and dataset.get("trade_authorization") is False
        and comparison.get("dataset_sha256") == EXPECTED_HASH
        and comparison.get("execution_enabled") is False
        and sensitivity.get("dataset_sha256") == EXPECTED_HASH
        and sensitivity.get("execution_enabled") is False
        and sensitivity.get("trade_authorization") is False,
        "Research source verification mismatch"
    )

    dates = sorted(
        dataset["full_standard_session_dates"]
    )
    require(len(dates) == 17, "Session count mismatch")

    case_results = []
    counts = {}

    for symbol in ("SPY", "QQQ"):
        raw_path = (
            FOLDER
            / f"yahoo_{symbol.lower()}_m5_raw_v001.json"
        )

        require(
            before[str(raw_path)]
            == comparison["raw_provider_responses"][
                symbol
            ]["sha256"],
            symbol + ": Yahoo source fingerprint mismatch"
        )

        wb = webull_sessions(
            dataset, symbol, dates
        )
        yh = yahoo_sessions(
            raw_path, symbol, dates
        )

        validate_sessions(wb, dates, symbol + " Webull")
        validate_sessions(yh, dates, symbol + " Yahoo")

        for day in dates:
            require(
                [x["utc"] for x in wb[day]]
                == [x["utc"] for x in yh[day]],
                symbol + ": candle timestamps differ"
            )

        webull_events = scan_events(wb, dates)
        yahoo_events = scan_events(yh, dates)

        expected = sensitivity["comparisons"][symbol]

        common = set(webull_events) & set(yahoo_events)

        changed_outcomes = sum(
            webull_events[key]["outcome"]
            != yahoo_events[key]["outcome"]
            for key in common
        )

        changed_levels = sum(
            abs(
                webull_events[key]["broken_level"]
                - yahoo_events[key]["broken_level"]
            ) > Decimal("0.02")
            for key in common
        )

        require(
            len(webull_events)
                == expected["webull_breakouts"]
            and len(yahoo_events)
                == expected["yahoo_breakouts"]
            and len(common)
                == expected["common_timestamps"]
            and changed_outcomes
                == expected["same_direction_outcome_changes"]
            and changed_levels
                == expected[
                    "same_direction_level_changes_over_2c"
                ],
            symbol + ": previous sensitivity audit mismatch"
        )

        counts[symbol] = {
            "webull_breakouts": len(webull_events),
            "yahoo_breakouts": len(yahoo_events),
            "common_breakouts": len(common),
            "changed_outcomes": changed_outcomes,
            "changed_broken_levels": changed_levels
        }

        for case_symbol, case_time in CASES:
            if case_symbol == symbol:
                case_results.append(
                    show_case(
                        symbol,
                        case_time,
                        wb,
                        yh
                    )
                )

    require(
        len(case_results) == 4,
        "Expected four disputed breakout observations"
    )

    require(
        all(
            fingerprint(path) == before[str(path)]
            for path in paths
        ),
        "A protected source changed"
    )

    result = {
        "version": "PROVIDER_EDGE_CASES_V001",
        "generated_utc": datetime.now(
            timezone.utc
        ).isoformat(),
        "dataset_sha256": EXPECTED_HASH,
        "sensitivity_report_sha256": before[
            str(SENSITIVITY)
        ],
        "cases_examined": len(case_results),
        "provider_event_reconciliation": counts,
        "cases": case_results,
        "independent_price_accuracy_established": False,
        "vendor_conventions_resolved": False,
        "out_of_sample_validation": False,
        "historical_research_only": True,
        "trade_signal": False,
        "execution_enabled": False,
        "trade_authorization": False
    }

    os.umask(0o077)

    with OUTPUT.open("x") as file:
        json.dump(result, file, indent=2)
        file.write("\n")

    OUTPUT.chmod(0o600)

    print("\n--- EDGE-CASE RECONCILIATION ---")
    print("Four disputed observations inspected: PASS")
    print("Original breakout counts reproduced: PASS")
    print("Outcome-change counts reproduced: PASS")
    print("Broken-level discrepancies reproduced: PASS")
    print("Protected source files: UNCHANGED")
    print("Provider accuracy established: NO")
    print("Historical analysis only: YES")
    print(
        "Saved: journal/price_verification/"
        "provider_edge_cases_v001.json"
    )

    print("\nPROVIDER EDGE-CASE AUDIT PASSED")
    print("WEBULL REQUESTS: NONE")
    print("YAHOO REQUESTS: NONE")
    print("AI REQUESTS: NONE")
    print("ORDER EXECUTION: DISABLED")


if __name__ == "__main__":
    main()
