"""Offline end-to-end testing of dual-provider mapping."""

import copy
import hashlib
import json
import os
from datetime import datetime, timezone
from pathlib import Path

from prepare_forward_evaluation_v001 import synthetic_capture
from forward_ingestion_v001 import verify_plan
from dual_provider_mapping_v001 import (
    verify_mapping, FORMATS, SYMBOLS, FIELDS
)

ROOT = Path(__file__).resolve().parent
FORWARD = ROOT / "journal/forward_evaluation_v001"
OUTPUT = FORWARD / "dual_provider_mapping_audit_v001.json"


def require(ok, message):
    if not ok:
        raise ValueError(
            "DUAL-PROVIDER AUDIT STOPPED: " + message
        )


def encode(value):
    return json.dumps(
        value, separators=(",", ":")
    ).encode()


def synthetic_raw_sources(capture):
    """Generate compatible, synthetic provider fixtures."""
    raw_sources = {}

    for provider in FORMATS:
        capture["sources"][provider]["format"] = (
            FORMATS[provider]
        )

        for symbol in SYMBOLS:
            bars = capture["sources"][provider][
                "bars"
            ][symbol]

            if provider == "WEBULL":
                raw_object = {
                    "source": "SYNTHETIC_TEST_ONLY",
                    "symbol": symbol,
                    "timeframe": "M5",
                    "research_only": True,
                    "execution_enabled": False,
                    "trade_authorization": False,
                    "bars": bars
                }
            else:
                raw_object = {
                    "chart": {
                        "error": None,
                        "result": [{
                            "meta": {
                                "symbol": symbol,
                                "exchangeTimezoneName":
                                    "America/New_York"
                            },
                            "timestamp": [
                                int(
                                    datetime.fromisoformat(
                                        bar["time"]
                                    ).timestamp()
                                )
                                for bar in bars
                            ],
                            "indicators": {
                                "quote": [{
                                    field: [
                                        bar[field] for bar in bars
                                    ]
                                    for field in FIELDS
                                }]
                            }
                        }]
                    }
                }

            raw = encode(raw_object)
            raw_sources[(provider, symbol)] = raw

            capture["sources"][provider][
                "raw_sha256"
            ][symbol] = hashlib.sha256(
                raw
            ).hexdigest()

    return raw_sources


def expect_rejection(capture, sources, label):
    try:
        verify_mapping(
            capture, sources, synthetic=True
        )
    except (ValueError, KeyError, TypeError):
        print(label + ": REJECTED AS EXPECTED")
    else:
        raise ValueError(
            "Unsafe source mapping accepted: " + label
        )


def main():
    print(
        "\n=== JAHVASCRIPT DUAL-PROVIDER MAPPING AUDIT ==="
    )

    require(
        not OUTPUT.exists(),
        "V001 mapping audit already exists"
    )

    before = verify_plan()

    capture = synthetic_capture()
    capture["capture_kind"] = "PROVIDER_CAPTURE_V001"
    capture["synthetic_test_only"] = True

    raw = synthetic_raw_sources(capture)

    result = verify_mapping(
        capture, raw, synthetic=True
    )

    require(
        result["verified_candles"] == 312
        and result["raw_to_normalized_mapping"] == "PASS"
        and result["future_holdout_admission"] is False
        and result["vendor_origin_authenticated"] is False,
        "Valid synthetic mapping was not reconciled safely"
    )

    print("Webull research-archive parser integration: PASS")
    print("Yahoo chart-response parser integration: PASS")
    print("Synthetic provider/symbol combinations: 4")
    print("Raw-to-normalized candle checks: 312 / 312")

    modified = copy.deepcopy(capture)
    modified["sources"]["WEBULL"]["bars"][
        "SPY"
    ][0]["close"] = "770.01"

    expect_rejection(
        modified, raw,
        "Modified normalized candle"
    )

    altered_raw = dict(raw)
    modified_yahoo = json.loads(
        raw[("YAHOO", "QQQ")]
    )
    modified_yahoo["chart"]["result"][0][
        "indicators"
    ]["quote"][0]["close"][0] = "700.01"

    altered_raw[("YAHOO", "QQQ")] = encode(
        modified_yahoo
    )

    expect_rejection(
        capture, altered_raw,
        "Changed raw file without hash update"
    )

    forged = copy.deepcopy(capture)
    forged["sources"]["YAHOO"]["raw_sha256"][
        "QQQ"
    ] = hashlib.sha256(
        altered_raw[("YAHOO", "QQQ")]
    ).hexdigest()

    expect_rejection(
        forged, altered_raw,
        "Changed raw file with matching new hash"
    )

    wrong_symbol = dict(raw)
    altered = json.loads(
        raw[("WEBULL", "SPY")]
    )
    altered["symbol"] = "QQQ"

    wrong_symbol[("WEBULL", "SPY")] = encode(
        altered
    )

    forged = copy.deepcopy(capture)
    forged["sources"]["WEBULL"]["raw_sha256"][
        "SPY"
    ] = hashlib.sha256(
        wrong_symbol[("WEBULL", "SPY")]
    ).hexdigest()

    expect_rejection(
        forged, wrong_symbol,
        "Incorrect Webull instrument"
    )

    wrong_format = copy.deepcopy(capture)
    wrong_format["sources"]["WEBULL"]["format"] = (
        "UNVERIFIED_LIVE_HTTP"
    )

    expect_rejection(
        wrong_format, raw,
        "Unverified live Webull schema"
    )

    require(
        verify_plan() == before,
        "Protected dataset, protocol, or manifest changed"
    )

    report = {
        "version": "DUAL_PROVIDER_MAPPING_AUDIT_V001",
        "generated_utc": datetime.now(
            timezone.utc
        ).isoformat(),
        "protected_plan_fingerprints": before,
        "supported_formats": FORMATS,
        "synthetic_source_pairs_verified": 4,
        "synthetic_candles_verified": 312,
        "negative_tests_passed": 5,
        "raw_to_normalized_mapping_fixture_test": "PASS",
        "real_provider_response_mapping_tested": False,
        "live_webull_http_schema_verified": False,
        "vendor_origin_authenticated": False,
        "official_exchange_calendar_verified": False,
        "future_real_sessions_admitted": 0,
        "future_evaluation_started": False,
        "synthetic_raw_data_persisted": False,
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

    require(
        verify_plan() == before,
        "Protected source changed after audit"
    )

    print("\n--- FINAL AUDIT ---")
    print("Supported-format mapping: VERIFIED ON FIXTURES")
    print("Negative mapping tests: 5/5 PASS")
    print("Synthetic source files saved: NONE")
    print("Original research files: UNCHANGED")
    print("Forward evaluation manifest: UNCHANGED")
    print("Live Webull raw-response format: NOT VERIFIED")
    print("Official exchange calendar: NOT VERIFIED")
    print("Real future sessions admitted: 0")
    print(
        "Saved: journal/forward_evaluation_v001/"
        "dual_provider_mapping_audit_v001.json"
    )

    print("\nDUAL-PROVIDER MAPPING AUDIT PASSED")
    print("WEBULL REQUESTS: NONE")
    print("YAHOO REQUESTS: NONE")
    print("AI REQUESTS: NONE")
    print("ORDER EXECUTION: DISABLED")


if __name__ == "__main__":
    main()
