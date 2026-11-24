"""Test pinned NYSE schedule and integration with ingestion."""

import copy
import hashlib
import json
import os
from datetime import date, datetime, time, timedelta, timezone
from pathlib import Path

from nyse_calendar_v001 import (
    ET, scheduled_session, validate_scheduled_timestamps
)
from prepare_forward_evaluation_v001 import (
    synthetic_capture, validate_capture
)
from forward_ingestion_v001 import verify_plan

ROOT = Path(__file__).resolve().parent
JOURNAL = ROOT / "journal"
FORWARD = JOURNAL / "forward_evaluation_v001"
OUTPUT = FORWARD / "nyse_calendar_audit_v001.json"

PROTECTED = [
    JOURNAL / "datasets/M5_SPY_QQQ_v001.json",
    JOURNAL / "price_verification/"
    "frozen_research_protocol_v001.json",
    FORWARD / "evaluation_manifest_v001.json"
]


def require(ok, message):
    if not ok:
        raise ValueError("CALENDAR AUDIT FAILED: " + message)


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def check(day, kind, bars, open_hour=None):
    result = scheduled_session(date.fromisoformat(day))

    require(
        result["kind"] == kind
        and result["expected_bars"] == bars,
        day + ": unexpected NYSE schedule"
    )

    if open_hour is not None:
        require(
            result["open_utc"].hour == open_hour,
            day + ": UTC opening time mismatch"
        )

    print(
        day, "|", kind,
        "|", bars, "five-minute candles: PASS"
    )


def reject(fn, label):
    try:
        fn()
    except (ValueError, KeyError, TypeError):
        print(label + ": REJECTED AS EXPECTED")
    else:
        raise ValueError(
            "Invalid exchange session accepted: " + label
        )


def fixture_for(day):
    """Re-date existing synthetic in-memory bars."""
    capture = synthetic_capture()
    capture["session_date"] = day.isoformat()

    start = datetime.combine(
        day, time(9, 30), tzinfo=ET
    )

    retrieved = datetime.combine(
        day, time(17, 0), tzinfo=ET
    ).astimezone(timezone.utc).isoformat()

    for source in capture["sources"].values():
        source["retrieved_utc"] = retrieved

        for bars in source["bars"].values():
            for i, bar in enumerate(bars):
                bar["time"] = (
                    start + timedelta(minutes=5 * i)
                ).isoformat()

    return capture


def main():
    print("\n=== JAHVASCRIPT NYSE CALENDAR AUDIT ===")

    require(
        not OUTPUT.exists(),
        "V001 calendar audit report already exists"
    )

    before_plan = verify_plan()
    before = {
        str(path): sha(path) for path in PROTECTED
    }

    print("\n--- OFFICIAL PUBLISHED SCHEDULE ---")

    check("2026-10-22", "STANDARD", 78, 13)
    check("2026-10-30", "STANDARD", 78, 13)
    check("2026-11-02", "STANDARD", 78, 14)
    check("2026-11-11", "STANDARD", 78, 14)
    check("2026-11-26", "CLOSED_HOLIDAY", 0)
    check("2026-11-27", "EARLY_CLOSE", 42, 14)
    check("2026-12-24", "EARLY_CLOSE", 42, 14)
    check("2026-12-25", "CLOSED_HOLIDAY", 0)
    check("2026-10-24", "CLOSED_WEEKEND", 0)

    print("\n--- SESSION TIMESTAMP VALIDATION ---")

    for day_string in (
        "2026-10-22",
        "2026-11-02",
        "2026-11-27",
        "2026-12-24"
    ):
        day = date.fromisoformat(day_string)
        schedule = scheduled_session(day)

        stamps = [
            schedule["open_utc"] + timedelta(minutes=5*i)
            for i in range(schedule["expected_bars"])
        ]

        require(
            validate_scheduled_timestamps(
                day, stamps
            )["kind"] == schedule["kind"],
            "Valid scheduled timestamps rejected"
        )

        reject(
            lambda d=day, s=stamps:
                validate_scheduled_timestamps(d, s[:-1]),
            day_string + " missing last candle"
        )

        if len(stamps) > 2:
            altered = stamps.copy()
            altered[1] = altered[0]
            reject(
                lambda d=day, s=altered:
                    validate_scheduled_timestamps(d, s),
                day_string + " duplicate timestamp"
            )

    print("\n--- FORWARD INGESTION INTEGRATION ---")

    require(
        validate_capture(
            fixture_for(date(2026, 11, 2))
        )["structural_gate"] == "PASS",
        "DST-adjusted full session rejected"
    )
    print("Full session after daylight-saving change: PASS")

    require(
        validate_capture(
            fixture_for(date(2026, 11, 11))
        )["structural_gate"] == "PASS",
        "Veterans Day should be a trading session"
    )
    print("Open bank-holiday session: PASS")

    reject(
        lambda: validate_capture(
            fixture_for(date(2026, 11, 26))
        ),
        "Thanksgiving trading capture"
    )

    reject(
        lambda: validate_capture(
            fixture_for(date(2026, 11, 27))
        ),
        "Black Friday as 78-candle session"
    )

    reject(
        lambda: validate_capture(
            fixture_for(date(2026, 12, 24))
        ),
        "Christmas Eve as 78-candle session"
    )

    reject(
        lambda: scheduled_session(
            date(2027, 1, 4)
        ),
        "Unverified calendar year"
    )

    require(
        verify_plan() == before_plan
        and all(
            sha(path) == before[str(path)]
            for path in PROTECTED
        ),
        "Research data or manifest modified"
    )

    report = {
        "version": "NYSE_CALENDAR_AUDIT_V001",
        "created_utc": datetime.now(
            timezone.utc
        ).isoformat(),
        "calendar_source":
            "https://www.nyse.com/trade/hours-calendars",
        "calendar_type":
            "PINNED_PUBLISHED_2026_CASH_EQUITY_SCHEDULE",
        "first_supported_date": "2026-10-22",
        "last_supported_date": "2026-12-31",
        "standard_bars": 78,
        "early_close_bars": 42,
        "official_published_schedule_tests": 9,
        "timestamp_acceptance": "PASS",
        "daylight_saving_time": "PASS",
        "forward_capture_calendar_gate": "PASS",
        "unexpected_special_closures_monitored": False,
        "official_schedule_may_change": True,
        "scheduled_early_closes_admitted_to_78bar_holdout":
            False,
        "real_sessions_admitted": 0,
        "future_evaluation_started": False,
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
        verify_plan() == before_plan
        and all(
            sha(path) == before[str(path)]
            for path in PROTECTED
        ),
        "Protected sources changed after calendar audit"
    )

    print("\n--- FINAL RESULT ---")
    print("Published 2026 NYSE calendar checks: 9/9 PASS")
    print("Regular and early-close timestamps: VERIFIED")
    print("DST conversion: VERIFIED")
    print("Forward ingestion calendar gate: VERIFIED")
    print("Unscheduled exchange closures: NOT MONITORED")
    print("Original Research V001: UNCHANGED")
    print("Real future sessions admitted: 0")
    print(
        "Saved: journal/forward_evaluation_v001/"
        "nyse_calendar_audit_v001.json"
    )

    print("\nNYSE CALENDAR AUDIT PASSED")
    print("WEBULL REQUESTS: NONE")
    print("YAHOO REQUESTS: NONE")
    print("AI REQUESTS: NONE")
    print("ORDER EXECUTION: DISABLED")


if __name__ == "__main__":
    main()
