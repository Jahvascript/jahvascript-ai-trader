"""JahvaScript collection preflight safety gate.

Checks calendar, collection timing, source fingerprints,
and supported raw-to-normalized mapping.

Live Webull HTTP parsing is NOT verified, so real
collection approval remains blocked.

No network, AI, signals, or orders.
"""

from datetime import date, datetime, timedelta, timezone

from dual_provider_mapping_v001 import verify_mapping
from prepare_forward_evaluation_v001 import validate_capture
from nyse_calendar_v001 import scheduled_session

BUFFER = timedelta(minutes=10)
CLOCK_ALLOWANCE = timedelta(minutes=5)


def require(ok, reason):
    if not ok:
        raise ValueError("COLLECTION GATE STOPPED: " + reason)


def aware(value):
    if isinstance(value, str):
        value = datetime.fromisoformat(value)

    require(
        isinstance(value, datetime)
        and value.tzinfo is not None
        and value.utcoffset() is not None,
        "Timezone-aware datetime required"
    )

    return value.astimezone(timezone.utc)


def assess(capture, raw_sources, observed_at,
           *, synthetic_fixture=False):
    """Return a test-only clearance or reject.

    Never authorizes real evaluation admission.
    """
    now = aware(observed_at)

    require(
        capture.get("synthetic_test_only")
            is synthetic_fixture,
        "Capture origin declaration mismatch"
    )

    # The existing structural validator already checks
    # 78 candles, RTH timestamps, OHLCV, and NYSE dates.
    validation = validate_capture(capture)
    day = date.fromisoformat(
        validation["session_date"]
    )

    session = scheduled_session(day)

    require(
        session["kind"] == "STANDARD"
        and session["expected_bars"] == 78,
        "Not an eligible standard NYSE session"
    )

    eligible_after = session["close_utc"] + BUFFER

    require(
        now >= eligible_after,
        "Collection attempted before close plus buffer"
    )

    for provider in ("WEBULL", "YAHOO"):
        retrieved = aware(
            capture["sources"][provider]["retrieved_utc"]
        )

        require(
            retrieved >= eligible_after,
            provider + ": response retrieved before "
            "the collection buffer elapsed"
        )

        require(
            retrieved <= now + CLOCK_ALLOWANCE,
            provider + ": retrieval timestamp lies "
            "in the future"
        )

    # Research archives are not proof that a live
    # Webull response uses the same schema. Real
    # approval must fail closed until that is audited.
    require(
        synthetic_fixture is True,
        "LIVE_WEBULL_HTTP_SCHEMA_NOT_VERIFIED: "
        "real collection approval blocked"
    )

    mapping = verify_mapping(
        capture,
        raw_sources,
        synthetic=True
    )

    require(
        mapping["raw_to_normalized_mapping"] == "PASS"
        and mapping["verified_candles"] == 312
        and mapping["future_holdout_admission"] is False,
        "Raw candle mapping incomplete"
    )

    return {
        "session_date": day.isoformat(),
        "schedule": "STANDARD",
        "market_close_utc":
            session["close_utc"].isoformat(),
        "eligible_after_utc":
            eligible_after.isoformat(),
        "mapping": "PASS",
        "mapped_candles": 312,
        "status": "SYNTHETIC_PREFLIGHT_PASS",
        "real_capture_approved": False,
        "holdout_admitted": False,
        "candle_finalization_verified": False,
        "live_webull_http_parser_verified": False,
        "trade_signal": False,
        "execution_enabled": False,
        "trade_authorization": False
    }
