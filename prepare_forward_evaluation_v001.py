"""JahvaScript future evaluation preparation.

Creates a frozen evaluation manifest and tests strict
session acceptance rules using SYNTHETIC in-memory bars.
Does NOT collect, generate, or evaluate real future data.
"""

import copy
import hashlib
import json
import os
from datetime import date, datetime, time, timedelta, timezone
from decimal import Decimal
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parent
JOURNAL = ROOT / "journal"
FROZEN = (
    JOURNAL / "price_verification/"
    "frozen_research_protocol_v001.json"
)
DATASET = JOURNAL / "datasets/M5_SPY_QQQ_v001.json"
FORWARD_DIR = JOURNAL / "forward_evaluation_v001"
MANIFEST = FORWARD_DIR / "evaluation_manifest_v001.json"

ET = ZoneInfo("America/New_York")
CUTOFF = date(2026, 10, 21)
TARGET_SESSIONS = 20
SYMBOLS = ("SPY", "QQQ")
PROVIDERS = ("WEBULL", "YAHOO")
CANDLE_SPACING = timedelta(minutes=5)

EXPECTED_DATASET_HASH = (
    "4c55e6c7961e9210f73f1468b20498fe20db4941cd27b10bd11753336292e3cb"
)


def require(condition, message):
    if not condition:
        raise ValueError("FORWARD GATE STOPPED: " + message)


def fingerprint(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def stamp(value):
    dt = datetime.fromisoformat(value)
    require(
        dt.tzinfo is not None
        and dt.utcoffset() is not None,
        "Timezone-aware timestamp required"
    )
    return dt.astimezone(timezone.utc)


def load_frozen():
    require(
        DATASET.is_file() and FROZEN.is_file(),
        "Missing original research or protocol"
    )
    require(
        fingerprint(DATASET) == EXPECTED_DATASET_HASH,
        "Dataset V001 fingerprint mismatch"
    )

    protocol = json.loads(FROZEN.read_text())
    plan = protocol["proposed_new_data_evaluation"]
    rules = protocol["rules_frozen_as_research_reference"]

    require(
        protocol.get("version")
            == "FROZEN_RESEARCH_PROTOCOL_V001"
        and protocol.get("exploratory_dataset_sha256")
            == EXPECTED_DATASET_HASH
        and protocol.get("exploratory_latest_observed_date")
            == CUTOFF.isoformat()
        and protocol.get("historical_research_only") is True
        and protocol.get("trade_signal") is False
        and protocol.get("execution_enabled") is False
        and protocol.get("trade_authorization") is False,
        "Unexpected frozen protocol configuration"
    )

    require(
        plan["status"] == "PLANNED_NOT_STARTED"
        and plan["target_complete_sessions"] == TARGET_SESSIONS
        and plan["no_rule_changes_during_evaluation"] is True
        and plan["no_exploratory_data_relabeling_as_new"] is True
        and rules["lookback_prior_candles"] == 12
        and rules["confirmation_horizon_bars"] == 3
        and rules["entry_margin_usd"] == "0.00"
        and rules["reversal_margin_usd"] == "0.00"
        and rules["reference_rules_are_trade_signals"] is False,
        "Frozen evaluation rules mismatch"
    )

    return protocol


def validate_bars(bars, session_day, label):
    require(
        isinstance(bars, list) and len(bars) == 78,
        label + ": exactly 78 candles required"
    )

    expected_start = datetime.combine(
        session_day,
        time(9, 30),
        tzinfo=ET
    )

    timestamps = []

    for index, bar in enumerate(bars):
        require(
            isinstance(bar, dict)
            and bar.get("session") == "RTH",
            label + ": expected RTH candle"
        )

        dt = stamp(bar["time"])
        expected = (
            expected_start
            + index * CANDLE_SPACING
        ).astimezone(timezone.utc)

        require(
            dt == expected,
            label + ": missing, duplicated, or "
            "misaligned candle at index "
            + str(index)
        )

        values = {}

        for field in (
            "open", "high", "low",
            "close", "volume"
        ):
            value = Decimal(str(bar[field]))

            require(
                value.is_finite(),
                label + ": invalid " + field
            )
            values[field] = value

        o = values["open"]
        h = values["high"]
        l = values["low"]
        c = values["close"]
        v = values["volume"]

        require(
            min(o, h, l, c) > 0
            and l <= min(o, c)
            and h >= max(o, c)
            and v >= 0,
            label + ": impossible OHLCV values"
        )

        timestamps.append(dt)

    return timestamps


def validate_capture(capture):
    """Accept only a fully documented paired session.

    A later collector must independently verify its raw
    response hashes before calling this acceptance gate.
    """
    require(
        capture.get("execution_enabled") is False
        and capture.get("trade_authorization") is False
        and capture.get("trade_signal") is False,
        "Trading permissions cannot be enabled"
    )

    session_day = date.fromisoformat(
        capture["session_date"]
    )

    require(
        session_day > CUTOFF,
        "Dataset V001 dates cannot enter the holdout"
    )

    from nyse_calendar_v001 import scheduled_session

    schedule = scheduled_session(session_day)

    require(
        schedule["kind"] == "STANDARD"
        and schedule["expected_bars"] == 78,
        "Only official standard 78-candle sessions "
        "qualify for this frozen evaluation; "
        "scheduled session type: " + schedule["kind"]
    )

    sources = capture["sources"]

    require(
        set(sources) == set(PROVIDERS),
        "Both named independent providers are required"
    )

    expected_close = schedule["close_utc"]

    all_times = {}

    for provider in PROVIDERS:
        source = sources[provider]

        require(
            source.get("provider") == provider
            and set(source["bars"]) == set(SYMBOLS)
            and set(source["raw_sha256"]) == set(SYMBOLS),
            provider + ": incomplete provenance"
        )

        retrieved = stamp(source["retrieved_utc"])

        require(
            retrieved >= expected_close,
            provider + ": retrieval predates "
            "the final candle's close"
        )

        for symbol in SYMBOLS:
            raw_hash = source["raw_sha256"][symbol]

            require(
                isinstance(raw_hash, str)
                and len(raw_hash) == 64
                and all(
                    character in "0123456789abcdef"
                    for character in raw_hash
                ),
                provider + ": invalid raw-source hash"
            )

            all_times[(provider, symbol)] = validate_bars(
                source["bars"][symbol],
                session_day,
                provider + " " + symbol
            )

    reference = all_times[("WEBULL", "SPY")]

    require(
        all(
            timeline == reference
            for timeline in all_times.values()
        ),
        "Provider/symbol candle timestamps differ"
    )

    # This validator establishes structural eligibility
    # only. Official exchange calendar, finalized market
    # data and matching raw bytes need separate evidence.
    return {
        "session_date": session_day.isoformat(),
        "structural_gate": "PASS",
        "candles_per_symbol_per_provider": 78,
        "timestamp_alignment": "PASS",
        "official_exchange_calendar_verified": False,
        "raw_response_hashes_independently_verified": False,
        "source_prices_independently_certified": False,
        "predictive_evaluation_performed": False,
        "trade_signal": False
    }


def expect_rejected(sample, label):
    try:
        validate_capture(sample)
    except (
        ValueError, KeyError, TypeError
    ):
        print(label + ": REJECTED AS EXPECTED")
    else:
        raise ValueError(
            "SELF-TEST FAILED: accepted " + label
        )


def synthetic_capture():
    """Fake test data created in RAM, never saved."""
    day = date(2026, 10, 22)
    start = datetime.combine(
        day, time(9, 30), tzinfo=ET
    )

    sources = {}

    for provider in PROVIDERS:
        bars = {}

        for symbol in SYMBOLS:
            price = Decimal(
                "700" if symbol == "QQQ" else "770"
            )

            candles = []

            for index in range(78):
                beginning = start + (
                    index * CANDLE_SPACING
                )
                close = price + Decimal(index) / 100

                candles.append({
                    "time": beginning.isoformat(),
                    "open": str(close),
                    "high": str(close + Decimal("0.05")),
                    "low": str(close - Decimal("0.05")),
                    "close": str(close),
                    "volume": "1000",
                    "session": "RTH"
                })

            bars[symbol] = candles

        sources[provider] = {
            "provider": provider,
            "retrieved_utc": (
                "2026-10-23T12:00:00+00:00"
            ),
            "raw_sha256": {
                symbol: "a" * 64
                for symbol in SYMBOLS
            },
            "bars": bars
        }

    return {
        "session_date": day.isoformat(),
        "sources": sources,
        "trade_signal": False,
        "execution_enabled": False,
        "trade_authorization": False
    }


def test_gates():
    print("\n--- SYNTHETIC ACCEPTANCE TESTS ---")

    valid = synthetic_capture()

    require(
        validate_capture(valid)["structural_gate"]
            == "PASS",
        "Complete synthetic session rejected"
    )

    print("Complete 78-candle paired session: PASS")

    stale = copy.deepcopy(valid)
    stale["session_date"] = CUTOFF.isoformat()
    expect_rejected(stale, "Pre-boundary session")

    incomplete = copy.deepcopy(valid)
    incomplete["sources"]["WEBULL"]["bars"]["SPY"].pop()
    expect_rejected(incomplete, "Missing final candle")

    duplicate = copy.deepcopy(valid)
    bars = duplicate["sources"]["WEBULL"]["bars"]["QQQ"]
    bars[10]["time"] = bars[9]["time"]
    expect_rejected(duplicate, "Duplicate timestamp")

    misaligned = copy.deepcopy(valid)
    bars = misaligned["sources"]["YAHOO"]["bars"]["SPY"]
    bars[20]["time"] = (
        datetime.fromisoformat(bars[20]["time"])
        + timedelta(minutes=1)
    ).isoformat()
    expect_rejected(misaligned, "Provider timestamp misalignment")

    invalid_price = copy.deepcopy(valid)
    invalid_price["sources"]["WEBULL"]["bars"]["SPY"][12]["high"] = "1"
    expect_rejected(invalid_price, "Invalid OHLC")

    unsafe = copy.deepcopy(valid)
    unsafe["execution_enabled"] = True
    expect_rejected(unsafe, "Enabled trading permission")

    early = copy.deepcopy(valid)
    early["sources"]["WEBULL"][
        "retrieved_utc"
    ] = "2026-10-22T13:00:00+00:00"
    expect_rejected(early, "Premature complete-session retrieval")

    print("Synthetic tests: 8/8 PASS")
    print("Synthetic candles written to disk: NONE")


def main():
    print(
        "\n=== JAHVASCRIPT FORWARD EVALUATION PREPARATION ==="
    )

    require(
        "journal/" in (
            ROOT / ".gitignore"
        ).read_text().splitlines(),
        "Research journal must be gitignored"
    )

    require(
        not MANIFEST.exists(),
        "Forward evaluation manifest already exists; "
        "refusing overwrite"
    )

    protected_before = {
        str(path): fingerprint(path)
        for path in (DATASET, FROZEN)
    }

    load_frozen()
    test_gates()

    plan = {
        "version": "FORWARD_EVALUATION_PLAN_V001",
        "created_utc": datetime.now(
            timezone.utc
        ).isoformat(),
        "frozen_protocol_sha256":
            protected_before[str(FROZEN)],
        "exploratory_dataset_sha256":
            EXPECTED_DATASET_HASH,
        "eligible_date_strictly_after":
            CUTOFF.isoformat(),
        "target_complete_sessions": TARGET_SESSIONS,
        "symbols": list(SYMBOLS),
        "required_providers": list(PROVIDERS),
        "candles_per_session_per_symbol": 78,
        "candle_timeframe": "5m",
        "session": "US regular trading hours",
        "timezone": "America/New_York",
        "research_rules_changed": False,
        "provider_raw_responses_to_be_preserved": True,
        "raw_sha256_verification_at_ingestion_required": True,
        "official_exchange_calendar_check_required": True,
        "candle_finalization_check_required": True,
        "partial_sessions_are_not_accepted_as_complete": True,
        "independent_provider_prices_may_disagree": True,
        "new_data_received": False,
        "accepted_real_sessions": 0,
        "evaluation_started": False,
        "evaluation_completed": False,
        "outcomes_examined": False,
        "status": "PREPARED_AWAITING_NEW_SESSIONS",
        "synthetic_test_data_included": False,
        "predictive_trading_edge_established": False,
        "trade_signal": False,
        "execution_enabled": False,
        "trade_authorization": False
    }

    require(
        all(
            fingerprint(path) == protected_before[str(path)]
            for path in (DATASET, FROZEN)
        ),
        "Protected source changed during preparation"
    )

    os.umask(0o077)
    FORWARD_DIR.mkdir(
        parents=True,
        exist_ok=True,
        mode=0o700
    )

    with MANIFEST.open("x") as handle:
        json.dump(plan, handle, indent=2)
        handle.write("\n")

    MANIFEST.chmod(0o600)

    require(
        json.loads(MANIFEST.read_text()) == plan,
        "Saved manifest differs from approved plan"
    )

    print("\n--- FUTURE EVALUATION MANIFEST ---")
    print("Frozen protocol linkage: PASS")
    print("Dataset V001 fingerprint: PASS")
    print("Eligible sessions: AFTER 2026-10-21")
    print("Target: 20 future complete sessions")
    print("Providers: WEBULL + YAHOO")
    print("Real future sessions accepted: 0")
    print("Evaluation started: NO")
    print("Original research files: UNCHANGED")
    print(
        "Saved: journal/forward_evaluation_v001/"
        "evaluation_manifest_v001.json"
    )

    print("\nFORWARD EVALUATION PREPARATION PASSED")
    print("WEBULL REQUESTS: NONE")
    print("YAHOO REQUESTS: NONE")
    print("AI REQUESTS: NONE")
    print("ORDER EXECUTION: DISABLED")


if __name__ == "__main__":
    main()
