"""JahvaScript calendar-aware forward collection planner.

Uses the pinned 2026 NYSE calendar and frozen research plan.
Does not request prices, admit sessions, or execute trades.
"""

import argparse
import copy
import hashlib
import json
import os
from datetime import date, datetime, timedelta
from pathlib import Path

from forward_ingestion_v001 import verify_plan
from nyse_calendar_v001 import (
    FIRST, LAST, scheduled_session
)

ROOT = Path(__file__).resolve().parent
FORWARD = ROOT / "journal/forward_evaluation_v001"

CALENDAR_AUDIT = FORWARD / "nyse_calendar_audit_v001.json"
OUTPUT = FORWARD / "collection_schedule_v001.json"

BUFFER_MINUTES = 10
TARGET = 20


def require(condition, message):
    if not condition:
        raise ValueError(
            "COLLECTION SCHEDULE STOPPED: " + message
        )


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def verify_sources():
    protected = verify_plan()

    require(
        CALENDAR_AUDIT.is_file(),
        "NYSE calendar audit report is missing"
    )

    audit = json.loads(CALENDAR_AUDIT.read_text())

    require(
        audit.get("version") == "NYSE_CALENDAR_AUDIT_V001"
        and audit.get("forward_capture_calendar_gate")
            == "PASS"
        and audit.get("daylight_saving_time") == "PASS"
        and audit.get("standard_bars") == 78
        and audit.get("early_close_bars") == 42
        and audit.get("real_sessions_admitted") == 0
        and audit.get("execution_enabled") is False
        and audit.get("trade_authorization") is False,
        "Calendar audit status or safety mismatch"
    )

    return {
        **protected,
        "calendar_audit": sha(CALENDAR_AUDIT)
    }


def build_schedule(fingerprints):
    rows = []
    current = FIRST

    while current <= LAST:
        session = scheduled_session(current)
        kind = session["kind"]

        standard = kind == "STANDARD"
        early = kind == "EARLY_CLOSE"

        require(
            (session["expected_bars"] == 78 if standard
             else session["expected_bars"] == 42 if early
             else session["expected_bars"] == 0),
            "Unexpected candle count"
        )

        # Ten minutes after the published closing time
        # is a collection-planning buffer, NOT proof
        # that a provider has finalized its candles.
        if session["close_utc"] is None:
            collection_after = None
        else:
            collection_after = (
                session["close_utc"]
                + timedelta(minutes=BUFFER_MINUTES)
            ).isoformat()

        rows.append({
            "date": current.isoformat(),
            "session_type": kind,
            "expected_5m_candles_per_symbol":
                session["expected_bars"],
            "scheduled_open_utc": (
                session["open_utc"].isoformat()
                if session["open_utc"] else None
            ),
            "scheduled_close_utc": (
                session["close_utc"].isoformat()
                if session["close_utc"] else None
            ),
            "planned_collection_after_utc":
                collection_after,
            "eligible_for_20_session_holdout": standard,
            "early_close_separate_archive": early,
            "collection_completed": False,
            "provider_finalization_verified": False
        })

        current += timedelta(days=1)

    candidates = [
        row["date"]
        for row in rows
        if row["eligible_for_20_session_holdout"]
    ]

    require(
        len(candidates) >= TARGET,
        "Insufficient verified calendar coverage"
    )

    return {
        "version": "FORWARD_COLLECTION_SCHEDULE_V001",
        "scope": {
            "first_date": FIRST.isoformat(),
            "last_date": LAST.isoformat(),
            "calendar":
                "PINNED_NYSE_PUBLISHED_2026_SCHEDULE",
            "unscheduled_closures_monitored": False
        },
        "source_fingerprints": fingerprints,
        "symbols": ["SPY", "QQQ"],
        "providers": ["WEBULL", "YAHOO"],
        "target_complete_sessions": TARGET,
        "target_session_dates": candidates[:TARGET],
        "target_last_planned_date": candidates[TARGET-1],
        "collection_buffer_minutes": BUFFER_MINUTES,
        "buffer_guarantees_finalization": False,
        "calendar_days": rows,
        "early_close_dates": [
            row["date"] for row in rows
            if row["session_type"] == "EARLY_CLOSE"
        ],
        "closed_holiday_dates": [
            row["date"] for row in rows
            if row["session_type"] == "CLOSED_HOLIDAY"
        ],
        "status": "SCHEDULE_PREPARED_NOT_COLLECTING",
        "actual_sessions_collected": 0,
        "actual_sessions_admitted": 0,
        "future_evaluation_started": False,
        "live_webull_http_parser_verified": False,
        "live_provider_delivery_verified": False,
        "source_finalization_verified": False,
        "trading_edge_established": False,
        "trade_signal": False,
        "execution_enabled": False,
        "trade_authorization": False
    }


def test_schedule(plan):
    print("\n--- CALENDAR PLANNING TESTS ---")

    by_day = {
        row["date"]: row
        for row in plan["calendar_days"]
    }

    require(
        len(by_day) == len(plan["calendar_days"]),
        "Duplicate calendar dates"
    )

    def check(day, kind, count, eligible):
        row = by_day[day]
        require(
            row["session_type"] == kind
            and row["expected_5m_candles_per_symbol"]
                == count
            and row["eligible_for_20_session_holdout"]
                is eligible,
            "Incorrect schedule classification: " + day
        )
        print(day, kind, "PASS")

    check("2026-10-22", "STANDARD", 78, True)
    check("2026-11-02", "STANDARD", 78, True)
    check("2026-11-11", "STANDARD", 78, True)
    check("2026-11-26", "CLOSED_HOLIDAY", 0, False)
    check("2026-11-27", "EARLY_CLOSE", 42, False)
    check("2026-12-24", "EARLY_CLOSE", 42, False)
    check("2026-12-25", "CLOSED_HOLIDAY", 0, False)

    target = plan["target_session_dates"]

    require(
        len(target) == TARGET
        and len(set(target)) == TARGET
        and target == sorted(target)
        and all(
            by_day[day]["session_type"] == "STANDARD"
            for day in target
        ),
        "Target contains invalid or duplicate sessions"
    )

    require(
        target[0] == "2026-10-22"
        and target[-1] == "2026-11-18",
        "Unexpected first 20 planned session dates"
    )

    print("Twenty complete-session dates: PASS")

    # DST must change UTC market-open timestamps
    # without changing the local exchange opening time.
    require(
        by_day["2026-10-30"]["scheduled_open_utc"]
            .endswith("13:30:00+00:00")
        and by_day["2026-11-02"]["scheduled_open_utc"]
            .endswith("14:30:00+00:00"),
        "Daylight-saving transition incorrect"
    )

    print("Daylight-saving adjustment: PASS")

    require(
        all(
            row["collection_completed"] is False
            and row["provider_finalization_verified"] is False
            for row in plan["calendar_days"]
        ),
        "Future data incorrectly marked as collected"
    )

    print("Uncollected sessions stay uncollected: PASS")

    # Ensure a changed target date doesn't silently
    # pass the original 20-session condition.
    altered = copy.deepcopy(plan)
    altered["target_session_dates"][0] = "2026-11-27"

    require(
        any(
            by_day[day]["session_type"] != "STANDARD"
            for day in altered["target_session_dates"]
        ),
        "Negative eligibility test did not detect change"
    )

    print("Early-close target substitution: DETECTED")


def save_or_verify(plan):
    OUTPUT.parent.mkdir(parents=True, exist_ok=True)

    if OUTPUT.exists():
        require(
            OUTPUT.is_file() and not OUTPUT.is_symlink(),
            "Existing schedule path is not a regular file"
        )

        saved = json.loads(OUTPUT.read_text())

        require(
            saved == plan,
            "Existing schedule differs from the frozen "
            "calendar plan. Original preserved."
        )

        print("Existing schedule: VERIFIED, NOT OVERWRITTEN")
        return

    os.umask(0o077)

    with OUTPUT.open("x") as handle:
        json.dump(plan, handle, indent=2)
        handle.write("\n")
        handle.flush()
        os.fsync(handle.fileno())

    OUTPUT.chmod(0o600)

    require(
        json.loads(OUTPUT.read_text()) == plan,
        "Saved schedule verification failed"
    )

    print("New schedule: SAVED AND VERIFIED")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--date",
        help="Inspect one date within pinned calendar coverage"
    )
    args = parser.parse_args()

    print(
        "\n=== JAHVASCRIPT FORWARD COLLECTION READINESS ==="
    )

    before = verify_sources()
    plan = build_schedule(before)
    test_schedule(plan)

    if args.date:
        requested = date.fromisoformat(args.date)
        matches = [
            row for row in plan["calendar_days"]
            if row["date"] == requested.isoformat()
        ]

        require(
            len(matches) == 1,
            "Date outside supported calendar"
        )

        print("\n--- REQUESTED SESSION ---")
        print(json.dumps(matches[0], indent=2))
    else:
        save_or_verify(plan)

    require(
        verify_sources() == before,
        "Protected research sources changed"
    )

    print("\n--- COLLECTION READINESS ---")
    print("First planned full session:",
          plan["target_session_dates"][0])
    print("Twentieth planned full session:",
          plan["target_last_planned_date"])
    print("Target complete sessions: 20")
    print("Scheduled early closes tracked:",
          len(plan["early_close_dates"]))
    print("Scheduled holidays tracked:",
          len(plan["closed_holiday_dates"]))
    print("Real sessions collected: 0")
    print("Real sessions admitted: 0")
    print("Unscheduled closure monitoring: NOT ENABLED")
    print("Live Webull response parser: NOT VERIFIED")
    print("Candle finalization: NOT VERIFIED")
    print("Automatic market collection: DISABLED")
    print("Research V001: UNCHANGED")
    print("Order execution: DISABLED")

    print("\nCALENDAR-AWARE COLLECTION READINESS PASSED")
    print("WEBULL REQUESTS: NONE")
    print("YAHOO REQUESTS: NONE")
    print("AI REQUESTS: NONE")
    print("ORDER EXECUTION: DISABLED")


if __name__ == "__main__":
    main()
