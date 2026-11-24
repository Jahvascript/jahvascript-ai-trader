"""Verify normalized candles against source bytes.

Supported formats:
- Yahoo historical chart JSON
- Webull saved research-archive JSON

This does not authenticate vendor origin, verify Webull's
live HTTP schema, or admit data into the future holdout.
"""

import hashlib
import json
from datetime import datetime, timezone
from decimal import Decimal

from prepare_forward_evaluation_v001 import validate_capture
from provider_raw_parsers_v001 import parse_yahoo
from webull_archive_parser_v001 import parse_archive

FORMATS = {
    "WEBULL": "WEBULL_RESEARCH_ARCHIVE_JSON",
    "YAHOO": "YAHOO_CHART_JSON"
}
SYMBOLS = ("SPY", "QQQ")
FIELDS = ("open", "high", "low", "close", "volume")


def require(ok, reason):
    if not ok:
        raise ValueError("RAW MAPPING STOPPED: " + reason)


def canonical_time(value):
    dt = datetime.fromisoformat(value)
    require(
        dt.tzinfo is not None and dt.utcoffset() is not None,
        "Timestamp must include timezone"
    )
    return dt.astimezone(timezone.utc).isoformat()


def verify_mapping(capture, raw_sources, *, synthetic=False):
    require(
        capture.get("synthetic_test_only") is synthetic,
        "Synthetic/real declaration mismatch"
    )

    gate = validate_capture(capture)
    day = gate["session_date"]

    expected_keys = {
        (provider, symbol)
        for provider in FORMATS
        for symbol in SYMBOLS
    }

    require(
        set(raw_sources) == expected_keys,
        "Exactly four raw provider/symbol sources required"
    )

    results = {}

    for provider in FORMATS:
        source = capture["sources"][provider]

        require(
            source.get("format") == FORMATS[provider],
            provider + ": unsupported raw schema"
        )

        for symbol in SYMBOLS:
            raw = raw_sources[(provider, symbol)]

            require(
                isinstance(raw, bytes)
                and 0 < len(raw) <= 16_000_000,
                "Invalid raw-source bytes"
            )

            actual_hash = hashlib.sha256(
                raw
            ).hexdigest()

            require(
                actual_hash == source["raw_sha256"][symbol],
                provider + " " + symbol
                + ": raw-source fingerprint mismatch"
            )

            if provider == "YAHOO":
                parsed = parse_yahoo(
                    raw, symbol, [day]
                )[day]
                parsed_by_time = {
                    row["time"]: row
                    for row in parsed
                }
            else:
                parsed_by_time = parse_archive(
                    raw, symbol, expected_count=78
                )

            declared = source["bars"][symbol]

            require(
                len(declared) == len(parsed_by_time) == 78,
                provider + " " + symbol
                + ": candle count differs"
            )

            matched = set()

            for candle in declared:
                key = canonical_time(candle["time"])

                require(
                    key in parsed_by_time,
                    provider + " " + symbol
                    + ": normalized timestamp absent from raw"
                )

                require(
                    key not in matched,
                    "Duplicate normalized candle"
                )

                actual = parsed_by_time[key]

                for field in FIELDS:
                    require(
                        Decimal(str(candle[field]))
                        == actual[field],
                        provider + " " + symbol + " "
                        + key + ": " + field
                        + " differs from raw source"
                    )

                if provider == "WEBULL":
                    require(
                        actual["session"] == candle["session"],
                        "Webull session classification differs"
                    )

                matched.add(key)

            require(
                matched == set(parsed_by_time),
                "Raw and normalized timestamp sets differ"
            )

            results[provider + "_" + symbol] = {
                "raw_sha256": actual_hash,
                "candles_verified": 78,
                "raw_to_normalized_mapping": "PASS"
            }

    return {
        "session_date": day,
        "provider_symbol_results": results,
        "verified_candles": 312,
        "raw_to_normalized_mapping": "PASS",
        "supported_webull_format":
            "SAVED_RESEARCH_ARCHIVE_ONLY",
        "vendor_origin_authenticated": False,
        "official_market_calendar_verified": False,
        "future_holdout_admission": False,
        "trade_signal": False,
        "execution_enabled": False
    }
