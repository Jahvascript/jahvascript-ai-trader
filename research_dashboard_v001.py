"""JahvaScript read-only historical research dashboard.

Reports verified source integrity and committed checkpoint
health. No data collection, AI requests, or order execution.
"""

import argparse
import hashlib
import json
import sys
from collections import Counter
from pathlib import Path

from research_health_v001 import inspect

ROOT = Path(__file__).resolve().parent
JOURNAL = ROOT / "journal"

DATASET = JOURNAL / "datasets/M5_SPY_QQQ_v001.json"
RESEARCH = JOURNAL / "expanded_research_v001.json"
CLOCK = JOURNAL / "rvol_clock_normalized_v001.json"
MARKET_STATE = JOURNAL / "market_state_v001.json"
REPLAY = JOURNAL / "market_replay_v001.json"
AUDIT = JOURNAL / "market_replay_timeline_audit_v001.json"

DEFAULT_CHECKPOINT = (
    JOURNAL / "research_session_v001.json"
)

EXPECTED_DATASET_HASH = (
    "4c55e6c7961e9210f73f1468b20498fe20db4941cd27b10bd11753336292e3cb"
)

EXPECTED_OUTCOMES = Counter({
    "CONFIRMED_REVERSAL": 155,
    "NO_FAILURE_WITHIN_HORIZON": 155,
    "CENSORED_SESSION_END": 18
})


def require(condition, message):
    if not condition:
        raise ValueError(
            "DASHBOARD INTEGRITY STOP: " + message
        )


def load_verified(path):
    raw = path.read_bytes()
    return json.loads(raw), hashlib.sha256(raw).hexdigest()


def collect(checkpoint):
    files = (
        DATASET, RESEARCH, CLOCK,
        MARKET_STATE, REPLAY, AUDIT
    )

    hashes_before = {
        str(path): hashlib.sha256(
            path.read_bytes()
        ).hexdigest()
        for path in files
    }

    dataset, dataset_hash = load_verified(DATASET)
    research, research_hash = load_verified(RESEARCH)
    clock, clock_hash = load_verified(CLOCK)
    state, state_hash = load_verified(MARKET_STATE)
    replay, replay_hash = load_verified(REPLAY)
    audit, audit_hash = load_verified(AUDIT)

    require(
        dataset_hash == EXPECTED_DATASET_HASH,
        "Dataset V001 fingerprint mismatch"
    )

    require(
        dataset.get("dataset_version") == "M5_SPY_QQQ_v001"
        and dataset.get("historical_research_only") is True
        and dataset.get("execution_enabled") is False
        and dataset.get("trade_authorization") is False
        and dataset.get("untouched_out_of_sample_test") is False,
        "Dataset safety or version mismatch"
    )

    dates = sorted(
        dataset["full_standard_session_dates"]
    )

    require(
        len(dates) == 17
        and dates == sorted(set(dates)),
        "Unexpected historical session coverage"
    )

    for symbol in ("SPY", "QQQ"):
        require(
            len(dataset["symbols"][symbol]["bars"]) == 1400,
            symbol + ": unexpected historical candle count"
        )

    require(
        research.get("dataset_sha256") == dataset_hash
        and research.get("out_of_sample") is False
        and research.get("execution_enabled") is False
        and research.get("trade_authorization") is False,
        "Expanded research verification mismatch"
    )

    require(
        clock.get("dataset_sha256") == dataset_hash
        and clock.get("research_sha256") == research_hash
        and clock.get("eligible_events") == 239
        and clock.get("execution_enabled") is False
        and clock.get("trade_authorization") is False,
        "Clock RVOL source mismatch"
    )

    require(
        state.get("dataset_sha256") == dataset_hash
        and state.get("expanded_research_sha256")
            == research_hash
        and state.get("clock_rvol_sha256") == clock_hash
        and state.get("events_integrated") == 239
        and state.get("features_use_future_candles") is False
        and state.get("execution_enabled") is False
        and state.get("trade_authorization") is False,
        "Market-state integration mismatch"
    )

    require(
        replay.get("dataset_sha256") == dataset_hash
        and replay.get("complete_sessions_replayed") == 17
        and replay.get(
            "breakouts_selected_without_future_outcomes"
        ) is True
        and replay.get("replay_is_historical_not_live") is True
        and replay.get("out_of_sample") is False
        and replay.get("execution_enabled") is False
        and replay.get("trade_authorization") is False
        and replay.get("trade_signal") is False,
        "Historical replay verification mismatch"
    )

    outcome_counts = Counter()
    breakout_counts = {}

    for symbol in ("SPY", "QQQ"):
        block = replay["symbols"][symbol]
        events = block["events"]
        transitions = block["transitions"]

        expected_count = (
            173 if symbol == "SPY" else 155
        )

        require(
            len(events) == expected_count
            and len(transitions) == 2 * expected_count
            and all(
                e.get("trade_signal") is False
                for e in events
            ),
            symbol + ": unexpected replay events"
        )

        actual = Counter(
            e["outcome"] for e in events
        )

        require(
            dict(actual) == block["outcome_counts"],
            symbol + ": replay outcomes mismatch"
        )

        outcome_counts.update(actual)
        breakout_counts[symbol] = len(events)

    require(
        outcome_counts == EXPECTED_OUTCOMES,
        "Combined replay outcomes mismatch"
    )

    require(
        audit.get("dataset_sha256") == dataset_hash
        and audit.get("replay_sha256") == replay_hash
        and audit.get("events_verified") == 328
        and audit.get("transitions_verified") == 656
        and audit.get("event_timing_verified") is True
        and audit.get("feature_prefix_reconstruction") is True
        and audit.get("execution_enabled") is False
        and audit.get("trade_authorization") is False,
        "Independent replay audit mismatch"
    )

    session = inspect(checkpoint)

    hashes_after = {
        str(path): hashlib.sha256(
            path.read_bytes()
        ).hexdigest()
        for path in files
    }

    require(
        hashes_before == hashes_after,
        "Protected research source changed during inspection"
    )

    return {
        "system": "JAHVASCRIPT_RESEARCH_V001",
        "dataset": {
            "status": "VERIFIED",
            "version": "M5_SPY_QQQ_v001",
            "sha256": dataset_hash,
            "symbols": ["SPY", "QQQ"],
            "bars_per_symbol": 1400,
            "complete_sessions": len(dates),
            "first_complete_session": dates[0],
            "last_complete_session": dates[-1],
            "source_prices_independently_verified": False
        },
        "research": {
            "expanded_breakouts": 328,
            "integrated_feature_events": 239,
            "market_state_integrity": "VERIFIED",
            "replay_integrity": "VERIFIED",
            "independent_timeline_audit": "PASSED",
            "breakouts": breakout_counts,
            "outcomes": dict(outcome_counts),
            "transitions_verified": 656,
            "out_of_sample_validated": False,
            "predictive_edge_established": False
        },
        "session": session,
        "safety": {
            "historical_data_only": True,
            "live_feed_verified": False,
            "ai_requests": False,
            "market_api_requests": False,
            "trade_signals_enabled": False,
            "order_execution_enabled": False
        },
        "dashboard_is_read_only": True
    }


def display(report):
    dataset = report["dataset"]
    research = report["research"]
    session = report["session"]
    safety = report["safety"]

    print("\n" + "=" * 49)
    print("     JAHVASCRIPT | RESEARCH OPERATIONS")
    print("=" * 49)

    print("\n[ HISTORICAL DATASET ]")
    print(
        "Integrity:",
        dataset["status"]
    )
    print(
        "Version:",
        dataset["version"]
    )
    print(
        "Historical candles:",
        dataset["bars_per_symbol"],
        "per symbol"
    )
    print(
        "Complete sessions:",
        dataset["complete_sessions"]
    )
    print(
        "Period:",
        dataset["first_complete_session"],
        "through",
        dataset["last_complete_session"]
    )

    print("\n[ RESEARCH PIPELINE ]")
    print(
        "Market-state integrity:",
        research["market_state_integrity"]
    )
    print(
        "Replay timeline audit:",
        research["independent_timeline_audit"]
    )
    print(
        "Historical breakout observations:",
        research["expanded_breakouts"]
    )
    print(
        "Integrated feature records:",
        research["integrated_feature_events"]
    )
    print(
        "Verified lifecycle transitions:",
        research["transitions_verified"]
    )

    print("\n[ RESEARCH SESSION ]")
    print("Status:", session["status"])
    print(
        "Checkpoint:",
        session["checkpoint"]
    )

    if "committed_transitions" in session:
        print(
            "Committed progress:",
            session["committed_transitions"],
            "/",
            session["total_transitions"]
        )
        print(
            "Recoverable remaining transitions:",
            session["remaining_transitions"]
        )
        print(
            "Committed breakout records:",
            session["recorded_breakouts"]
        )
        print(
            "Pending outcomes:",
            session["pending_outcomes"]
        )
        print(
            "Writer lock:",
            session["writer_lock"]
        )
    elif session["status"] == "MISSING":
        print(
            "No persistent checkpoint at this path."
        )
        print(
            "Historical research files are still verified."
        )
    elif session["status"] == "INVALID_CHECKPOINT":
        print(
            "WARNING: Checkpoint failed integrity verification."
        )
        print(
            "Reason:",
            session.get("error", "Unavailable")
        )

    print("\n[ SAFETY & VALIDATION ]")
    print("Historical research only: YES")
    print("Source price verification: NOT COMPLETED")
    print("Out-of-sample validation: NOT COMPLETED")
    print("Predictive trading edge: NOT ESTABLISHED")
    print("Live market feed: NOT VERIFIED")
    print("AI/API requests made by dashboard: NONE")
    print("Order execution: DISABLED")
    print("Dashboard source modifications: NONE")
    print("=" * 49)


def main():
    parser = argparse.ArgumentParser(
        description=__doc__
    )
    parser.add_argument(
        "--checkpoint",
        type=Path,
        default=DEFAULT_CHECKPOINT
    )
    parser.add_argument(
        "--json",
        action="store_true"
    )

    args = parser.parse_args()
    report = collect(args.checkpoint)

    if args.json:
        print(json.dumps(report, indent=2))
    else:
        display(report)

    if report["session"]["status"] == "INVALID_CHECKPOINT":
        return 2

    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except (
        ValueError,
        OSError,
        KeyError,
        TypeError
    ) as error:
        print(str(error), file=sys.stderr)
        sys.exit(1)
