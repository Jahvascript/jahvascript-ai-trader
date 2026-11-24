"""JahvaScript V001 operational readiness audit.

Verifies historical research artifacts and persistent
checkpoint integrity. No network calls, AI, or orders.
"""

import hashlib
import json
import os
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

import replay_checkpoint_v001 as cp
from research_dashboard_v001 import collect

ROOT = Path(__file__).resolve().parent
JOURNAL = ROOT / "journal"

DATASET = JOURNAL / "datasets/M5_SPY_QQQ_v001.json"
EXPANDED = JOURNAL / "expanded_research_v001.json"
CLOCK = JOURNAL / "rvol_clock_normalized_v001.json"
STATE = JOURNAL / "market_state_v001.json"
REPLAY = JOURNAL / "market_replay_v001.json"
TIMELINE = JOURNAL / "market_replay_timeline_audit_v001.json"
CHECKPOINT = JOURNAL / "research_session_v001.json"
OUTPUT = JOURNAL / "operational_readiness_v001.json"

EXPECTED_DATASET_HASH = (
    "4c55e6c7961e9210f73f1468b20498fe20db4941cd27b10bd11753336292e3cb"
)

PROTECTED = (
    DATASET, EXPANDED, CLOCK, STATE,
    REPLAY, TIMELINE, CHECKPOINT
)

EXPECTED_OUTCOMES = Counter({
    "CONFIRMED_REVERSAL": 155,
    "NO_FAILURE_WITHIN_HORIZON": 155,
    "CENSORED_SESSION_END": 18
})


def require(ok, message):
    if not ok:
        raise ValueError("READINESS AUDIT STOPPED: " + message)


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main():
    print(
        "\n=== JAHVASCRIPT OPERATIONAL READINESS AUDIT ==="
    )

    require(
        "journal/" in (
            ROOT / ".gitignore"
        ).read_text().splitlines(),
        "Research journal must be gitignored"
    )

    require(
        not OUTPUT.exists(),
        "V001 readiness report already exists"
    )

    require(
        all(path.is_file() for path in PROTECTED),
        "A required research artifact is missing"
    )

    before = {
        str(path): sha(path)
        for path in PROTECTED
    }

    require(
        sha(DATASET) == EXPECTED_DATASET_HASH,
        "Historical dataset fingerprint mismatch"
    )

    dataset = json.loads(DATASET.read_text())
    expanded = json.loads(EXPANDED.read_text())
    clock = json.loads(CLOCK.read_text())
    state = json.loads(STATE.read_text())
    replay = json.loads(REPLAY.read_text())
    timeline = json.loads(TIMELINE.read_text())

    print("\n--- SOURCE INTEGRITY ---")

    require(
        dataset.get("historical_research_only") is True
        and dataset.get("execution_enabled") is False
        and dataset.get("trade_authorization") is False,
        "Historical dataset configuration mismatch"
    )

    require(
        expanded.get("dataset_sha256")
            == EXPECTED_DATASET_HASH
        and expanded.get("out_of_sample") is False
        and expanded.get("execution_enabled") is False
        and expanded.get("trade_authorization") is False,
        "Expanded research configuration mismatch"
    )

    require(
        clock.get("dataset_sha256")
            == EXPECTED_DATASET_HASH
        and clock.get("research_sha256") == sha(EXPANDED)
        and clock.get("eligible_events") == 239
        and clock.get("execution_enabled") is False
        and clock.get("trade_authorization") is False,
        "Clock RVOL verification mismatch"
    )

    require(
        state.get("dataset_sha256")
            == EXPECTED_DATASET_HASH
        and state.get("expanded_research_sha256")
            == sha(EXPANDED)
        and state.get("clock_rvol_sha256") == sha(CLOCK)
        and state.get("events_integrated") == 239
        and state.get("features_use_future_candles") is False
        and state.get("execution_enabled") is False
        and state.get("trade_authorization") is False,
        "Integrated market-state verification mismatch"
    )

    require(
        replay.get("dataset_sha256")
            == EXPECTED_DATASET_HASH
        and replay.get("replay_is_historical_not_live") is True
        and replay.get(
            "breakouts_selected_without_future_outcomes"
        ) is True
        and replay.get("out_of_sample") is False
        and replay.get("trade_signal") is False
        and replay.get("execution_enabled") is False
        and replay.get("trade_authorization") is False,
        "Replay verification mismatch"
    )

    require(
        timeline.get("dataset_sha256")
            == EXPECTED_DATASET_HASH
        and timeline.get("replay_sha256") == sha(REPLAY)
        and timeline.get("events_verified") == 328
        and timeline.get("transitions_verified") == 656
        and timeline.get("event_timing_verified") is True
        and timeline.get("feature_prefix_reconstruction") is True
        and timeline.get("execution_enabled") is False
        and timeline.get("trade_authorization") is False,
        "Independent timeline audit mismatch"
    )

    print("Dataset fingerprint: PASS")
    print("Expanded historical research: PASS")
    print("Dual-RVOL research linkage: PASS")
    print("Integrated market-state linkage: PASS")
    print("Independent replay timeline audit: PASS")

    print("\n--- PERSISTENT SESSION ---")

    stream, source_hash = cp.load_verified_stream(ROOT)
    saved = cp.restore(CHECKPOINT, stream, source_hash)

    require(
        len(stream) == 656
        and saved["cursor"] == 656
        and len(saved["records"]) == 328,
        "Persistent checkpoint not fully committed"
    )

    outcomes = Counter(
        row["outcome"]
        for row in saved["records"].values()
    )

    require(
        outcomes == EXPECTED_OUTCOMES,
        "Persistent checkpoint outcomes mismatch"
    )

    require(
        saved == cp.replay_prefix(
            stream, len(stream), source_hash
        ),
        "Persistent state not independently reproducible"
    )

    require(
        CHECKPOINT.stat().st_mode & 0o777 == 0o600,
        "Private checkpoint permissions mismatch"
    )

    dashboard = collect(CHECKPOINT)
    session = dashboard["session"]

    require(
        session["status"] == "COMPLETE_IDLE"
        and session["writer_lock"] == "FREE"
        and session["committed_transitions"] == 656
        and session["remaining_transitions"] == 0
        and session["recorded_breakouts"] == 328
        and session["pending_outcomes"] == 0,
        "Operational dashboard and checkpoint disagree"
    )

    print("Session health: COMPLETE_IDLE")
    print("Writer lock: FREE")
    print("Committed transitions: 656 / 656")
    print("Persistent breakout records: 328")
    print("Unresolved historical outcomes: 0")
    print("Deterministic checkpoint reconstruction: PASS")
    print("Private checkpoint permissions: PASS")

    print("\n--- READINESS GATES ---")

    gates = {
        "local_historical_research": {
            "status": "READY",
            "basis": (
                "Verified historical replay, independent "
                "timeline audit and durable checkpoint"
            )
        },
        "independent_market_price_verification": {
            "status": "NOT_COMPLETED",
            "basis": (
                "Recorded price candles have not been "
                "independently compared with an external source"
            )
        },
        "untouched_out_of_sample_evaluation": {
            "status": "NOT_COMPLETED",
            "basis": (
                "Existing historical periods have been used "
                "for exploratory research"
            )
        },
        "live_market_data_pipeline": {
            "status": "NOT_VERIFIED",
            "basis": (
                "Historical replay does not establish live "
                "delivery, finalization or gap handling"
            )
        },
        "paper_trading_validation": {
            "status": "NOT_COMPLETED",
            "basis": (
                "No validated execution simulation or "
                "forward-trading evaluation"
            )
        },
        "real_order_execution": {
            "status": "DISABLED",
            "basis": (
                "Research workflows provide no trade "
                "authorization; repository-wide order paths "
                "have not been audited by this script"
            )
        }
    }

    for name, gate in gates.items():
        print(
            name.upper() + ":",
            gate["status"]
        )

    require(
        gates["local_historical_research"]["status"] == "READY"
        and all(
            gate["status"] != "READY"
            for name, gate in gates.items()
            if name != "local_historical_research"
        ),
        "Readiness gates incorrectly elevated"
    )

    require(
        all(sha(path) == before[str(path)] for path in PROTECTED),
        "Protected research source changed"
    )

    report = {
        "generated_utc": datetime.now(
            timezone.utc
        ).isoformat(),
        "version": "OPERATIONAL_READINESS_V001",
        "dataset_sha256": EXPECTED_DATASET_HASH,
        "source_fingerprints": {
            path.relative_to(ROOT).as_posix(): before[str(path)]
            for path in PROTECTED
        },
        "persistent_cursor": saved["cursor"],
        "historical_events": len(saved["records"]),
        "verified_transitions": len(stream),
        "outcomes": dict(outcomes),
        "gates": gates,
        "historical_only": True,
        "out_of_sample": False,
        "source_prices_verified": False,
        "predictive_trading_edge_established": False,
        "live_trading_ready": False,
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
        all(sha(path) == before[str(path)] for path in PROTECTED),
        "Protected source changed during report creation"
    )

    print("\n--- FINAL RESULT ---")
    print("Historical research infrastructure: READY")
    print("Live forward testing: NOT YET READY")
    print("Real-money execution: DISABLED")
    print("Protected historical sources: UNCHANGED")
    print("Saved: journal/operational_readiness_v001.json")

    print("\nOPERATIONAL READINESS AUDIT PASSED")
    print("WEBULL REQUESTS: NONE")
    print("AI REQUESTS: NONE")
    print("ORDER EXECUTION: DISABLED")


if __name__ == "__main__":
    main()
