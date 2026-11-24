"""Freeze JahvaScript exploratory findings and future evaluation plan.

Offline, read-only research review except for writing one new
protocol artifact. Does not collect prices or authorize trading.
"""

import hashlib
import json
import os
from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parent
JOURNAL = ROOT / "journal"
FOLDER = JOURNAL / "price_verification"

DATASET = JOURNAL / "datasets/M5_SPY_QQQ_v001.json"
SENSITIVITY = FOLDER / "breakout_provider_sensitivity_v001.json"
THRESHOLDS = FOLDER / "breakout_threshold_sensitivity_v001.json"
SEPARATION = FOLDER / "threshold_separation_v001.json"
EDGE_CASES = FOLDER / "provider_edge_cases_v001.json"
OUTPUT = FOLDER / "frozen_research_protocol_v001.json"

PROTECTED = (
    DATASET, SENSITIVITY, THRESHOLDS,
    SEPARATION, EDGE_CASES
)

EXPECTED_HASH = (
    "4c55e6c7961e9210f73f1468b20498fe20db4941cd27b10bd11753336292e3cb"
)

ET = ZoneInfo("America/New_York")


def require(condition, message):
    if not condition:
        raise ValueError("PROTOCOL FREEZE STOPPED: " + message)


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def check_combined(actual, expected, description):
    require(
        actual == expected,
        description + ": historical result changed"
    )


def main():
    print("\n=== JAHVASCRIPT RESEARCH PROTOCOL FREEZE ===")

    require(
        "journal/" in (
            ROOT / ".gitignore"
        ).read_text().splitlines(),
        "Research journal must be gitignored"
    )

    require(
        not OUTPUT.exists(),
        "V001 protocol already exists; refusing overwrite"
    )

    require(
        all(path.is_file() for path in PROTECTED),
        "Required research artifact missing"
    )

    before = {
        str(path): sha(path)
        for path in PROTECTED
    }

    require(
        before[str(DATASET)] == EXPECTED_HASH,
        "Dataset V001 fingerprint mismatch"
    )

    dataset = json.loads(DATASET.read_text())
    sensitivity = json.loads(SENSITIVITY.read_text())
    thresholds = json.loads(THRESHOLDS.read_text())
    separation = json.loads(SEPARATION.read_text())
    cases = json.loads(EDGE_CASES.read_text())

    require(
        dataset.get("historical_research_only") is True
        and dataset.get("execution_enabled") is False
        and dataset.get("trade_authorization") is False
        and dataset.get("untouched_out_of_sample_test") is False,
        "Dataset research status changed"
    )

    for label, report in (
        ("Provider sensitivity", sensitivity),
        ("Margin sensitivity", thresholds),
        ("Threshold separation", separation),
        ("Provider edge cases", cases)
    ):
        require(
            report.get("dataset_sha256") == EXPECTED_HASH
            and report.get("execution_enabled") is False
            and report.get("trade_authorization") is False,
            label + ": source or safety mismatch"
        )

    require(
        cases["cases_examined"] == 4
        and cases["sensitivity_report_sha256"]
            == before[str(SENSITIVITY)]
        and separation["equal_margin_baselines_reproduced"]
            is True
        and separation["monotonicity_checks_passed"] is True
        and separation["recommended_thresholds"] is None,
        "Previous independent audit not reconciled"
    )

    dates = sorted(dataset["full_standard_session_dates"])

    require(
        len(dates) == 17
        and len(set(dates)) == 17
        and dates[0] == "2026-09-28"
        and dates[-1] == "2026-10-20",
        "Unexpected exploration session coverage"
    )

    grid = separation["grid"]
    require(len(grid) == 16, "Expected 16 sensitivity cases")

    baseline = grid["0.00/0.00"]["combined"]
    entry_only = grid["0.05/0.00"]["combined"]
    reversal_only = grid["0.00/0.05"]["combined"]
    both_strict = grid["0.05/0.05"]["combined"]

    print("\n--- BASELINE RECONCILIATION ---")

    check_combined(
        (
            baseline["webull"]["events"],
            baseline["yahoo"]["events"],
            baseline["shared_timestamps"],
            baseline["outcome_disagreements"],
            baseline["full_horizon_shared"],
            baseline["full_horizon_outcome_disagreements"]
        ),
        (328, 326, 326, 2, 304, 0),
        "Original zero-margin baseline"
    )

    check_combined(
        (
            entry_only["webull"]["events"],
            entry_only["yahoo"]["events"],
            entry_only["shared_timestamps"],
            entry_only["outcome_disagreements"],
            entry_only["full_horizon_shared"],
            entry_only["full_horizon_outcome_disagreements"]
        ),
        (264, 265, 263, 2, 245, 0),
        "Entry-only $0.05 sensitivity"
    )

    check_combined(
        (
            reversal_only["webull"]["events"],
            reversal_only["yahoo"]["events"],
            reversal_only["shared_timestamps"],
            reversal_only["outcome_disagreements"],
            reversal_only["full_horizon_shared"],
            reversal_only["full_horizon_outcome_disagreements"]
        ),
        (328, 326, 326, 1, 304, 1),
        "Reversal-only $0.05 sensitivity"
    )

    check_combined(
        (
            both_strict["webull"]["events"],
            both_strict["yahoo"]["events"],
            both_strict["shared_timestamps"],
            both_strict["outcome_disagreements"]
        ),
        (264, 265, 263, 0),
        "Combined $0.05 sensitivity"
    )

    print("Original Webull breakouts: 328")
    print("Original Yahoo breakouts: 326")
    print("Common breakout timestamps: 326")
    print("Full-horizon shared breakouts: 304")
    print("Full-horizon baseline outcome disagreements: 0")
    print("Exploratory margin combinations reconciled: 16")
    print("Threshold recommended from historical data: NONE")

    latest_observed = {}

    for symbol in ("SPY", "QQQ"):
        bars = dataset["symbols"][symbol]["bars"]

        require(
            len(bars) == 1400,
            symbol + ": candle coverage changed"
        )

        dt = datetime.fromisoformat(
            bars[-1]["time"]
        )

        require(
            dt.tzinfo is not None
            and dt.utcoffset() is not None,
            symbol + ": missing timestamp timezone"
        )

        latest_observed[symbol] = dt.astimezone(
            ET
        ).date().isoformat()

    require(
        latest_observed["SPY"] == "2026-10-21"
        and latest_observed["QQQ"] == "2026-10-21",
        "Unexpected snapshot end date"
    )

    print("\n--- FUTURE EVALUATION BOUNDARY ---")
    print("Last date present in V001: 2026-10-21")
    print("First allowable new session: AFTER 2026-10-21")
    print("Proposed evaluation duration: 20 complete sessions")
    print("Historical data from V001 reusable as holdout: NO")

    protocol = {
        "version": "FROZEN_RESEARCH_PROTOCOL_V001",
        "frozen_at_utc": datetime.now(
            timezone.utc
        ).isoformat(),
        "source_fingerprints": {
            path.relative_to(ROOT).as_posix(): before[str(path)]
            for path in PROTECTED
        },
        "exploratory_dataset": "M5_SPY_QQQ_v001",
        "exploratory_dataset_sha256": EXPECTED_HASH,
        "exploratory_complete_sessions": 17,
        "exploratory_latest_observed_date":
            "2026-10-21",
        "verified_baseline": {
            "webull_breakouts": 328,
            "yahoo_breakouts": 326,
            "shared_breakouts": 326,
            "outcome_disagreements": 2,
            "full_horizon_shared": 304,
            "full_horizon_outcome_disagreements": 0
        },
        "rules_frozen_as_research_reference": {
            "instruments": ["SPY", "QQQ"],
            "timeframe": "5m",
            "session": "US regular hours",
            "candle_available_at": "Five-minute candle close",
            "lookback_prior_candles": 12,
            "up_breakout":
                "previous close <= prior 12-bar high "
                "AND current close > prior 12-bar high",
            "down_breakout":
                "previous close >= prior 12-bar low "
                "AND current close < prior 12-bar low",
            "entry_margin_usd": "0.00",
            "reversal_margin_usd": "0.00",
            "confirmation_horizon_bars": 3,
            "censoring":
                "If a reversal occurs before the session "
                "ends, record it; otherwise censor when "
                "fewer than 3 later session bars exist",
            "provider_comparison":
                "Compare Webull and independently sourced "
                "Yahoo 5-minute RTH OHLCV data",
            "reference_rules_are_trade_signals": False
        },
        "proposed_new_data_evaluation": {
            "status": "PLANNED_NOT_STARTED",
            "eligible_session_date_rule":
                "Strictly later than 2026-10-21",
            "target_complete_sessions": 20,
            "source_files_separate_from_v001": True,
            "session_integrity_requirement":
                "78 chronological five-minute RTH bars "
                "per symbol per complete session",
            "timestamp_alignment_requirement":
                "Check both vendors' shared candle timestamps",
            "missing_or_partial_sessions":
                "Record separately, never silently fill",
            "primary_descriptive_checks": [
                "Provider breakout timestamp agreement",
                "Provider outcome agreement on matching "
                "breakouts with complete confirmation windows",
                "Censoring and missing-outcome counts",
                "Differences in support/resistance thresholds"
            ],
            "secondary_descriptive_checks": [
                "EMA 9/21/55 context",
                "Session VWAP context",
                "Rolling 5-bar RVOL",
                "Same-clock-time RVOL using five "
                "previously completed sessions",
                "Time-of-day and cross-market dependence"
            ],
            "no_rule_changes_during_evaluation": True,
            "no_exploratory_data_relabeling_as_new": True,
            "holdout_results_cannot_be_used_to_claim "
            "profitable_trading_without_further_evidence": True
        },
        "unresolved_gates": {
            "exchange_grade_source_price_verification": False,
            "new_unseen_data_collected": False,
            "new_unseen_data_evaluated": False,
            "live_data_delivery_validated": False,
            "paper_trading_validated": False,
            "predictive_trading_edge_established": False
        },
        "original_margin_sensitivity_is_exploratory": True,
        "recommended_trading_threshold": None,
        "historical_research_only": True,
        "trade_signal": False,
        "execution_enabled": False,
        "trade_authorization": False
    }

    require(
        all(sha(path) == before[str(path)] for path in PROTECTED),
        "Protected files changed during protocol freeze"
    )

    os.umask(0o077)

    with OUTPUT.open("x") as file:
        json.dump(protocol, file, indent=2)
        file.write("\n")

    OUTPUT.chmod(0o600)

    saved = json.loads(OUTPUT.read_text())

    require(
        saved == protocol,
        "Saved protocol does not match constructed protocol"
    )

    require(
        all(sha(path) == before[str(path)] for path in PROTECTED),
        "Protected files changed after protocol freeze"
    )

    print("\n--- FINAL FREEZE VERIFICATION ---")
    print("Historical findings: DOCUMENTED")
    print("Baseline research definitions: FROZEN")
    print("Prospective evaluation: PLANNED, NOT STARTED")
    print("New market data fetched: NONE")
    print("Original datasets/reports: UNCHANGED")
    print("Predictive trading edge established: NO")
    print(
        "Saved: journal/price_verification/"
        "frozen_research_protocol_v001.json"
    )

    print("\nRESEARCH PROTOCOL FREEZE PASSED")
    print("WEBULL REQUESTS: NONE")
    print("YAHOO REQUESTS: NONE")
    print("AI REQUESTS: NONE")
    print("ORDER EXECUTION: DISABLED")


if __name__ == "__main__":
    main()
