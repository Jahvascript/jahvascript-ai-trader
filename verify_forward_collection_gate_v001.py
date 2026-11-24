"""Offline forward collection gate verification."""

import copy
import hashlib
import json
import os
from datetime import datetime, timezone
from pathlib import Path

from forward_collection_gate_v001 import assess
from forward_collection_schedule_v001 import verify_sources
from prepare_forward_evaluation_v001 import synthetic_capture
from verify_dual_provider_mapping_v001 import synthetic_raw_sources

ROOT = Path(__file__).resolve().parent
FORWARD = ROOT / "journal/forward_evaluation_v001"
SCHEDULE = FORWARD / "collection_schedule_v001.json"
OUTPUT = FORWARD / "collection_safety_audit_v001.json"

TEST_NOW = datetime(
    2026, 10, 23, 14, 0,
    tzinfo=timezone.utc
)


def require(ok, reason):
    if not ok:
        raise ValueError(
            "COLLECTION SAFETY AUDIT FAILED: " + reason
        )


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def expect_rejection(capture, raw, now, label,
                     *, synthetic=True):
    try:
        assess(
            capture, raw, now,
            synthetic_fixture=synthetic
        )
    except (ValueError, KeyError, TypeError):
        print(label + ": REJECTED AS EXPECTED")
    else:
        raise ValueError(
            "Unsafe preflight accepted: " + label
        )


def main():
    print(
        "\n=== JAHVASCRIPT FORWARD COLLECTION SAFETY AUDIT ==="
    )

    before = verify_sources()

    require(
        SCHEDULE.is_file(),
        "Collection schedule missing"
    )

    schedule_hash = sha(SCHEDULE)
    schedule = json.loads(SCHEDULE.read_text())

    require(
        schedule.get("version")
            == "FORWARD_COLLECTION_SCHEDULE_V001"
        and schedule.get("source_fingerprints") == before
        and schedule.get("target_complete_sessions") == 20
        and len(schedule["target_session_dates"]) == 20
        and schedule["target_session_dates"][0]
            == "2026-10-22"
        and schedule["target_session_dates"][-1]
            == "2026-11-18"
        and schedule.get("actual_sessions_collected") == 0
        and schedule.get("actual_sessions_admitted") == 0
        and schedule.get("execution_enabled") is False,
        "Frozen collection schedule mismatch"
    )

    print("Frozen protocol linkage: PASS")
    print("NYSE collection schedule: PASS")
    print("Future sessions collected: 0")

    capture = synthetic_capture()
    capture["capture_kind"] = "PROVIDER_CAPTURE_V001"
    capture["synthetic_test_only"] = True

    raw = synthetic_raw_sources(capture)

    # The default fixture's retrieval time is Oct. 23.
    # The market closed Oct. 22 at 20:00 UTC.
    approved = assess(
        capture,
        raw,
        TEST_NOW,
        synthetic_fixture=True
    )

    require(
        approved["status"]
            == "SYNTHETIC_PREFLIGHT_PASS"
        and approved["mapped_candles"] == 312
        and approved["eligible_after_utc"]
            == "2026-10-22T20:10:00+00:00"
        and approved["real_capture_approved"] is False
        and approved["holdout_admitted"] is False,
        "Synthetic preflight reconciliation failed"
    )

    print("\n--- SUCCESSFUL SYNTHETIC PREFLIGHT ---")
    print("Session date:", approved["session_date"])
    print("Market close:", approved["market_close_utc"])
    print("Collection allowed after:",
          approved["eligible_after_utc"])
    print("Raw-to-normalized candles: 312 / 312")
    print("Synthetic preflight: PASS")
    print("Real capture approval: BLOCKED")

    print("\n--- SAFETY REJECTION TESTS ---")

    # The date is valid, but the collection window
    # has not elapsed.
    expect_rejection(
        capture, raw,
        datetime(
            2026, 10, 22, 20, 9,
            tzinfo=timezone.utc
        ),
        "Before collection buffer"
    )

    # Response must itself have been retrieved
    # after the buffer, not merely inspected later.
    modified = copy.deepcopy(capture)
    modified["sources"]["WEBULL"]["retrieved_utc"] = (
        "2026-10-22T20:05:00+00:00"
    )

    expect_rejection(
        modified, raw, TEST_NOW,
        "Response retrieved before buffer"
    )

    # A timestamp that appears to come from the
    # future cannot be trusted.
    modified = copy.deepcopy(capture)
    modified["sources"]["YAHOO"]["retrieved_utc"] = (
        "2026-10-24T12:00:00+00:00"
    )

    expect_rejection(
        modified, raw, TEST_NOW,
        "Future retrieval timestamp"
    )

    # Modified normalized values cannot be accepted
    # just because the old raw-file hash is valid.
    modified = copy.deepcopy(capture)
    modified["sources"]["WEBULL"]["bars"]["SPY"][
        0
    ]["close"] = "770.01"

    expect_rejection(
        modified, raw, TEST_NOW,
        "Changed normalized close"
    )

    # An unsupported Webull response format must
    # never be treated as the verified archive schema.
    modified = copy.deepcopy(capture)
    modified["sources"]["WEBULL"]["format"] = (
        "UNVERIFIED_LIVE_HTTP"
    )

    expect_rejection(
        modified, raw, TEST_NOW,
        "Unsupported Webull live format"
    )

    # The original research boundary cannot be
    # relabeled as new evaluation data.
    modified = copy.deepcopy(capture)
    modified["session_date"] = "2026-10-21"

    expect_rejection(
        modified, raw, TEST_NOW,
        "Previously explored session"
    )

    # Holiday and short-session attempts are
    # rejected by the existing NYSE calendar gate.
    for day, label in (
        ("2026-11-26", "NYSE holiday"),
        ("2026-11-27", "NYSE early close")
    ):
        modified = copy.deepcopy(capture)
        modified["session_date"] = day

        expect_rejection(
            modified, raw,
            datetime(
                2026, 11, 30, 18, 0,
                tzinfo=timezone.utc
            ),
            label
        )

    # Synthetic fixtures must not be relabeled as
    # real captured market data.
    modified = copy.deepcopy(capture)
    modified["synthetic_test_only"] = False

    expect_rejection(
        modified, raw, TEST_NOW,
        "Real-mode use of unverified Webull format",
        synthetic=False
    )

    require(
        verify_sources() == before
        and sha(SCHEDULE) == schedule_hash,
        "Protected schedule or research sources changed"
    )

    report = {
        "version": "FORWARD_COLLECTION_SAFETY_AUDIT_V001",
        "source_fingerprints": before,
        "collection_schedule_sha256": schedule_hash,
        "synthetic_mapping_checks": 312,
        "negative_cases_passed": 9,
        "collection_buffer_minutes": 10,
        "buffer_proves_provider_finalization": False,
        "synthetic_preflight": "PASS",
        "live_webull_response_format_verified": False,
        "real_capture_approval_enabled": False,
        "real_sessions_collected": 0,
        "holdout_sessions_admitted": 0,
        "automatic_collection_enabled": False,
        "trade_signal": False,
        "execution_enabled": False,
        "trade_authorization": False
    }

    # Repeat runs verify the existing audit rather
    # than overwrite or delete it.
    if OUTPUT.exists():
        require(
            OUTPUT.is_file()
            and not OUTPUT.is_symlink()
            and json.loads(OUTPUT.read_text()) == report,
            "Existing audit differs; original preserved"
        )
        print("\nExisting safety audit: VERIFIED")
    else:
        os.umask(0o077)

        with OUTPUT.open("x") as handle:
            json.dump(report, handle, indent=2)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())

        OUTPUT.chmod(0o600)
        print("\nSafety audit report: SAVED")

    require(
        verify_sources() == before
        and sha(SCHEDULE) == schedule_hash,
        "Protected sources changed after audit"
    )

    print("\n--- FINAL RESULT ---")
    print("Synthetic collection preflight: PASS")
    print("Unsafe scenarios rejected: 9 / 9")
    print("Actual candle finalization: NOT VERIFIED")
    print("Live Webull HTTP parser: NOT VERIFIED")
    print("Real collection approval: BLOCKED")
    print("Original Research V001: UNCHANGED")
    print("Real sessions admitted: 0")
    print(
        "Saved: journal/forward_evaluation_v001/"
        "collection_safety_audit_v001.json"
    )

    print("\nFORWARD COLLECTION SAFETY AUDIT PASSED")
    print("WEBULL REQUESTS: NONE")
    print("YAHOO REQUESTS: NONE")
    print("AI REQUESTS: NONE")
    print("ORDER EXECUTION: DISABLED")


if __name__ == "__main__":
    main()
