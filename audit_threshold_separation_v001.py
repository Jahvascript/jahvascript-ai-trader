"""JahvaScript independent entry/exit threshold sensitivity.

Uses frozen Webull V001 and saved Yahoo candles.
No API calls, AI requests, trading signals, or orders.
"""

import hashlib
import json
import os
from collections import Counter
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path

import audit_breakout_thresholds_v001 as source

ROOT = Path(__file__).resolve().parent
JOURNAL = ROOT / "journal"
FOLDER = JOURNAL / "price_verification"

DATASET = JOURNAL / "datasets/M5_SPY_QQQ_v001.json"
PREVIOUS = FOLDER / "breakout_threshold_sensitivity_v001.json"
OUTPUT = FOLDER / "threshold_separation_v001.json"

YAHOO = {
    symbol: FOLDER / f"yahoo_{symbol.lower()}_m5_raw_v001.json"
    for symbol in ("SPY", "QQQ")
}

MARGINS = tuple(
    Decimal(value)
    for value in ("0.00", "0.01", "0.02", "0.05")
)

EXPECTED_HASH = (
    "4c55e6c7961e9210f73f1468b20498fe20db4941cd27b10bd11753336292e3cb"
)


def require(ok, message):
    if not ok:
        raise ValueError("SEPARATION AUDIT STOPPED: " + message)


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def detect(sessions, dates, entry_margin, reversal_margin):
    """Breakout discovery sees no future candles."""
    events = {}

    for day in dates:
        bars = sessions[day]

        for i in range(12, len(bars)):
            prior = bars[i-12:i]
            resistance = max(b["high"] for b in prior)
            support = min(b["low"] for b in prior)
            previous_close = bars[i-1]["close"]
            close = bars[i]["close"]

            up = (
                previous_close <= resistance
                and close > resistance + entry_margin
            )
            down = (
                previous_close >= support
                and close < support - entry_margin
            )

            require(
                not (up and down),
                "Ambiguous breakout"
            )

            if not (up or down):
                continue

            direction = "UP" if up else "DOWN"
            level = resistance if up else support

            later = bars[i+1:min(i+4, len(bars))]
            confirmation = None

            # Only retrospective outcome evaluation
            # is permitted to inspect subsequent bars.
            for future in later:
                reversed_breakout = (
                    future["close"] < level - reversal_margin
                    if up
                    else future["close"] > level + reversal_margin
                )

                if reversed_breakout:
                    confirmation = future["time"].isoformat()
                    break

            if confirmation is not None:
                outcome = "CONFIRMED_REVERSAL"
            elif len(later) == 3:
                outcome = "NO_FAILURE_WITHIN_HORIZON"
            else:
                outcome = "CENSORED_SESSION_END"

            key = bars[i]["time"].isoformat()

            require(
                key not in events,
                "Duplicate event"
            )

            events[key] = {
                "direction": direction,
                "outcome": outcome,
                "confirmation": confirmation,
                "level": str(level),
                "full_horizon_available": len(later) == 3,
                "early_confirmed_in_short_window": (
                    outcome == "CONFIRMED_REVERSAL"
                    and len(later) < 3
                )
            }

    return events


def evaluate(webull, yahoo):
    a, b = set(webull), set(yahoo)
    shared = a & b

    same_direction = {
        key for key in shared
        if webull[key]["direction"] == yahoo[key]["direction"]
    }

    mismatches = sum(
        webull[key]["outcome"] != yahoo[key]["outcome"]
        for key in same_direction
    )

    both_full = {
        key for key in same_direction
        if webull[key]["full_horizon_available"]
        and yahoo[key]["full_horizon_available"]
    }

    full_mismatches = sum(
        webull[key]["outcome"] != yahoo[key]["outcome"]
        for key in both_full
    )

    def summarize(events):
        counts = Counter(
            item["outcome"] for item in events.values()
        )
        return {
            "events": len(events),
            "reversed": counts["CONFIRMED_REVERSAL"],
            "no_failure": counts["NO_FAILURE_WITHIN_HORIZON"],
            "censored": counts["CENSORED_SESSION_END"],
            "early_confirmation_without_full_horizon": sum(
                item["early_confirmed_in_short_window"]
                for item in events.values()
            )
        }

    union = a | b

    return {
        "webull": summarize(webull),
        "yahoo": summarize(yahoo),
        "shared_timestamps": len(shared),
        "same_direction_shared": len(same_direction),
        "opposite_direction_shared":
            len(shared) - len(same_direction),
        "webull_only": len(a - b),
        "yahoo_only": len(b - a),
        "outcome_disagreements": mismatches,
        "full_horizon_shared": len(both_full),
        "full_horizon_outcome_disagreements": full_mismatches,
        "union_events": len(union),
        "overlap_percent": (
            round(100 * len(shared) / len(union), 2)
            if union else None
        )
    }


def combine(results):
    names = (
        "events", "reversed", "no_failure",
        "censored", "early_confirmation_without_full_horizon"
    )
    result = {
        "webull": {},
        "yahoo": {}
    }

    for provider in ("webull", "yahoo"):
        for name in names:
            result[provider][name] = sum(
                results[symbol][provider][name]
                for symbol in ("SPY", "QQQ")
            )

    for name in (
        "shared_timestamps", "same_direction_shared",
        "opposite_direction_shared", "webull_only",
        "yahoo_only", "outcome_disagreements",
        "full_horizon_shared",
        "full_horizon_outcome_disagreements",
        "union_events"
    ):
        result[name] = sum(
            results[symbol][name]
            for symbol in ("SPY", "QQQ")
        )

    result["overlap_percent"] = round(
        100 * result["shared_timestamps"]
        / result["union_events"], 2
    )

    return result


def show(entry, reversal, result):
    print(
        f" Entry ${entry}, reversal ${reversal}"
        f" | W={result['webull']['events']}"
        f" Y={result['yahoo']['events']}"
        f" shared={result['shared_timestamps']}"
        f" overlap={result['overlap_percent']:.2f}%"
        f" outcome disagreements={result['outcome_disagreements']}"
    )


def main():
    print(
        "\n=== JAHVASCRIPT THRESHOLD SEPARATION AUDIT ==="
    )

    require(
        "journal/" in (
            ROOT / ".gitignore"
        ).read_text().splitlines(),
        "Research journal must be gitignored"
    )
    require(
        not OUTPUT.exists(),
        "V001 output already exists; refusing overwrite"
    )

    protected = [DATASET, PREVIOUS, *YAHOO.values()]
    before = {
        str(path): sha(path) for path in protected
    }

    require(
        before[str(DATASET)] == EXPECTED_HASH,
        "Dataset fingerprint mismatch"
    )

    dataset = json.loads(DATASET.read_text())
    previous = json.loads(PREVIOUS.read_text())

    require(
        dataset.get("historical_research_only") is True
        and dataset.get("execution_enabled") is False
        and dataset.get("trade_authorization") is False
        and previous.get("dataset_sha256") == EXPECTED_HASH
        and previous.get("execution_enabled") is False
        and previous.get("trade_authorization") is False
        and previous.get("recommended_margin") is None,
        "Research source configuration mismatch"
    )

    dates = sorted(dataset["full_standard_session_dates"])
    require(len(dates) == 17, "Session coverage changed")

    data = {}

    for symbol in ("SPY", "QQQ"):
        data[symbol] = {
            "webull": source.webull_sessions(
                dataset, symbol, dates
            ),
            "yahoo": source.yahoo_sessions(
                YAHOO[symbol], symbol, dates
            )
        }

        for day in dates:
            require(
                [x["time"] for x in data[symbol]["webull"][day]]
                == [x["time"] for x in data[symbol]["yahoo"][day]],
                "Provider timestamps differ"
            )

    grid = {}
    detected = {}

    # Preserve separate entry and reversal thresholds.
    for entry in MARGINS:
        for reversal in MARGINS:
            key = f"{entry}/{reversal}"
            by_symbol = {}
            detected[key] = {}

            for symbol in ("SPY", "QQQ"):
                a = detect(
                    data[symbol]["webull"],
                    dates, entry, reversal
                )
                b = detect(
                    data[symbol]["yahoo"],
                    dates, entry, reversal
                )

                detected[key][symbol] = (a, b)
                by_symbol[symbol] = evaluate(a, b)

                # Reproduce the previously audited
                # equal-margin experiment exactly.
                if entry == reversal:
                    expected = previous[
                        "results"
                    ][str(entry)][symbol]

                    require(
                        by_symbol[symbol]["webull"]["events"]
                            == expected["webull"]["events"]
                        and by_symbol[symbol]["yahoo"]["events"]
                            == expected["yahoo"]["events"]
                        and by_symbol[symbol]["shared_timestamps"]
                            == expected["common_timestamps"]
                        and by_symbol[symbol]["outcome_disagreements"]
                            == expected[
                                "different_outcomes_on_common"
                            ]
                        and by_symbol[symbol]["webull"]["reversed"]
                            == expected["webull"]["confirmed"]
                        and by_symbol[symbol]["yahoo"]["reversed"]
                            == expected["yahoo"]["confirmed"],
                        symbol + ": equal-margin baseline differs"
                    )

                    # Independent verification against
                    # the existing detection implementation.
                    require(
                        a == source.detect(
                            data[symbol]["webull"], dates, entry
                        )
                        and b == source.detect(
                            data[symbol]["yahoo"], dates, entry
                        ),
                        symbol + ": diagonal calculation mismatch"
                    )

            grid[key] = {
                "entry_margin": str(entry),
                "reversal_margin": str(reversal),
                "symbols": by_symbol,
                "combined": combine(by_symbol)
            }

    # Increasing entry margin cannot introduce a new
    # breakout when the reversal margin is held fixed.
    for reversal in MARGINS:
        for symbol in ("SPY", "QQQ"):
            for provider_index in (0, 1):
                previous_set = None

                for entry in MARGINS:
                    current = set(
                        detected[f"{entry}/{reversal}"][symbol][
                            provider_index
                        ]
                    )

                    if previous_set is not None:
                        require(
                            current <= previous_set,
                            "Stricter entry margin added breakouts"
                        )

                    previous_set = current

    # Increasing reversal margin cannot introduce new
    # confirmed reversals when entry selection is fixed.
    for entry in MARGINS:
        for symbol in ("SPY", "QQQ"):
            for provider_index in (0, 1):
                previous_confirmed = None

                for reversal in MARGINS:
                    events = detected[
                        f"{entry}/{reversal}"
                    ][symbol][provider_index]

                    confirmed = {
                        key for key, event in events.items()
                        if event["outcome"] == "CONFIRMED_REVERSAL"
                    }

                    if previous_confirmed is not None:
                        require(
                            confirmed <= previous_confirmed,
                            "Stricter reversal margin added reversals"
                        )

                    previous_confirmed = confirmed

    print("Sessions:", len(dates))
    print("Matched source timestamps: PASS")
    print("Previously audited diagonal cases: PASS")
    print("Monotonic entry and reversal checks: PASS")

    print("\n--- CHANGE ENTRY MARGIN ONLY ---")
    print("Reversal margin held at $0.00")
    for entry in MARGINS:
        show(
            entry, Decimal("0.00"),
            grid[f"{entry}/0.00"]["combined"]
        )

    print("\n--- CHANGE REVERSAL MARGIN ONLY ---")
    print("Entry margin held at $0.00")
    for reversal in MARGINS:
        show(
            Decimal("0.00"), reversal,
            grid[f"0.00/{reversal}"]["combined"]
        )

    print("\n--- CHANGE BOTH MARGINS ---")
    for margin in MARGINS:
        show(
            margin, margin,
            grid[f"{margin}/{margin}"]["combined"]
        )

    print("\n--- FULL-HORIZON COMPARISON ---")
    for key in (
        "0.00/0.00", "0.00/0.05",
        "0.05/0.00", "0.05/0.05"
    ):
        result = grid[key]["combined"]
        print(
            key,
            "| shared full-horizon:",
            result["full_horizon_shared"],
            "| outcome disagreements:",
            result["full_horizon_outcome_disagreements"]
        )

    require(
        all(sha(path) == before[str(path)] for path in protected),
        "Protected inputs changed"
    )

    report = {
        "version": "THRESHOLD_SEPARATION_V001",
        "created_utc": datetime.now(
            timezone.utc
        ).isoformat(),
        "dataset_sha256": EXPECTED_HASH,
        "source_hashes": {
            path.relative_to(ROOT).as_posix(): before[str(path)]
            for path in protected
        },
        "entry_margins_usd": [str(v) for v in MARGINS],
        "reversal_margins_usd": [str(v) for v in MARGINS],
        "grid": grid,
        "equal_margin_baselines_reproduced": True,
        "monotonicity_checks_passed": True,
        "reversal_outcomes_use_future_candles": True,
        "margins_are_exploratory": True,
        "recommended_thresholds": None,
        "source_prices_independently_certified": False,
        "untouched_out_of_sample_test": False,
        "predictive_trading_edge_established": False,
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

    print("\n16 entry/reversal threshold combinations: VERIFIED")
    print("Existing research and provider data: UNCHANGED")
    print("Threshold recommendation: NONE")
    print("Out-of-sample predictive test: NONE")
    print("Saved: journal/price_verification/"
          "threshold_separation_v001.json")

    print("\nTHRESHOLD SEPARATION AUDIT PASSED")
    print("WEBULL REQUESTS: NONE")
    print("YAHOO REQUESTS: NONE")
    print("AI REQUESTS: NONE")
    print("ORDER EXECUTION: DISABLED")


if __name__ == "__main__":
    main()
