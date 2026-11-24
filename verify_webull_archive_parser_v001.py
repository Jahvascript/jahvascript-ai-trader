"""Verify every archived Webull M5 candle against Dataset V001.

Performs fingerprint checks, independent parsing, full
dataset reconciliation, and in-memory negative tests.
"""

import copy
import hashlib
import json
import os
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

from webull_archive_parser_v001 import (
    parse_archive,
    parse_bar,
    merge_archives
)

ROOT = Path(__file__).resolve().parent
JOURNAL = ROOT / "journal"
DATASET = JOURNAL / "datasets/M5_SPY_QQQ_v001.json"
OUTPUT = (
    JOURNAL / "forward_evaluation_v001"
    / "webull_archive_parser_audit_v001.json"
)

ET = ZoneInfo("America/New_York")

EXPECTED_DATASET_HASH = (
    "4c55e6c7961e9210f73f1468b20498fe20db4941cd27b10bd11753336292e3cb"
)

ARCHIVES = {
    "SPY": (
        (
            "history/SPY_M5.json",
            500,
            "2485626377f01e9377a81230c7b5ca4ef7ad4827f473805a8b689014956c98b7"
        ),
        (
            "history/backfill/SPY_M5_backfill_001.json",
            100,
            "9de49dc0f8933232fba658633eb628020c8678a4f4f275db75d50971d5a2b9b3"
        ),
        (
            "history/backfill/batch_002/SPY_M5.json",
            400,
            "8eeadad7830e1557e556fdfdefd2759384b961361ad1c083e288a48c9542c500"
        ),
        (
            "history/backfill/batch_003/SPY_M5.json",
            400,
            "48d584371fdc81fe83a828066c3923e97ec23f594c784676c9d87c5805f043ff"
        )
    ),
    "QQQ": (
        (
            "history/QQQ_M5.json",
            500,
            "8046357f484191c27db0d1dc7c7525e86403339dd9abb2e4ce8fb3565b3aea99"
        ),
        (
            "history/backfill/QQQ_M5_backfill_001.json",
            100,
            "bc2911f933c8bbd3b4615de88a87562cfc417b6a60fb8ac4010d48d84e83ca3b"
        ),
        (
            "history/backfill/batch_002/QQQ_M5.json",
            400,
            "3b748979b4dad433dbaee855640a4223667e3456372dc804c461e647401e3e2c"
        ),
        (
            "history/backfill/batch_003/QQQ_M5.json",
            400,
            "16b7a3e799fe1be192d8bb857317b0543db976a2ab3bd10719d1526994ba9d9a"
        )
    )
}


def require(ok, message):
    if not ok:
        raise ValueError(
            "WEBULL ARCHIVE AUDIT STOPPED: " + message
        )


def sha(data):
    return hashlib.sha256(data).hexdigest()


def expect_rejection(payload, symbol, label):
    raw = json.dumps(payload).encode()

    try:
        parse_archive(raw, symbol)
    except (ValueError, KeyError, TypeError):
        print(label + ": REJECTED AS EXPECTED")
    else:
        raise ValueError(
            "Parser accepted invalid case: " + label
        )


def negative_tests(first_raw, symbol):
    original = json.loads(first_raw)

    wrong = copy.deepcopy(original)
    wrong["symbol"] = "WRONG"
    expect_rejection(wrong, symbol, "Wrong instrument")

    wrong = copy.deepcopy(original)
    wrong["timeframe"] = "D"
    expect_rejection(wrong, symbol, "Wrong timeframe")

    wrong = copy.deepcopy(original)
    wrong["execution_enabled"] = True
    expect_rejection(wrong, symbol, "Enabled execution")

    wrong = copy.deepcopy(original)
    wrong["bars"][1]["time"] = wrong["bars"][0]["time"]
    expect_rejection(wrong, symbol, "Duplicate timestamp")

    wrong = copy.deepcopy(original)
    del wrong["bars"][0]["close"]
    expect_rejection(wrong, symbol, "Missing closing price")

    wrong = copy.deepcopy(original)
    wrong["bars"][0]["high"] = "-1"
    expect_rejection(wrong, symbol, "Impossible OHLC")

    wrong = copy.deepcopy(original)
    dt = datetime.fromisoformat(wrong["bars"][0]["time"])
    wrong["bars"][0]["time"] = dt.replace(
        second=1
    ).isoformat()
    expect_rejection(wrong, symbol, "Unaligned timestamp")


def dataset_candles(dataset, symbol):
    candles = {}

    for bar in dataset["symbols"][symbol]["bars"]:
        parsed = parse_bar(bar)
        key = parsed["time"]

        require(
            key not in candles,
            symbol + ": duplicate frozen dataset timestamp"
        )

        candles[key] = parsed

    require(
        len(candles) == 1400,
        symbol + ": unexpected frozen candle count"
    )

    return candles


def main():
    print(
        "\n=== JAHVASCRIPT WEBULL ARCHIVE PARSER AUDIT ==="
    )

    require(
        "journal/" in (
            ROOT / ".gitignore"
        ).read_text().splitlines(),
        "Research journal must be gitignored"
    )

    require(
        not OUTPUT.exists(),
        "V001 archive audit already exists"
    )

    dataset_raw = DATASET.read_bytes()

    require(
        sha(dataset_raw) == EXPECTED_DATASET_HASH,
        "Frozen dataset fingerprint mismatch"
    )

    dataset = json.loads(dataset_raw)

    require(
        dataset.get("historical_research_only") is True
        and dataset.get("execution_enabled") is False
        and dataset.get("trade_authorization") is False,
        "Frozen research safety mismatch"
    )

    full_dates = set(
        dataset["full_standard_session_dates"]
    )

    require(
        len(full_dates) == 17,
        "Unexpected full-session coverage"
    )

    source_files = {}
    report_symbols = {}

    for symbol in ("SPY", "QQQ"):
        print("\n---", symbol, "---")

        batches = []
        first_raw = None

        for filename, count, expected_hash in ARCHIVES[symbol]:
            path = JOURNAL / filename

            require(
                path.is_file() and not path.is_symlink(),
                "Missing or linked archive: " + filename
            )

            raw = path.read_bytes()

            require(
                sha(raw) == expected_hash,
                "Archive fingerprint mismatch: " + filename
            )

            parsed = parse_archive(raw, symbol, count)

            require(
                len(parsed) == count,
                "Parser count mismatch: " + filename
            )

            batches.append((filename, parsed))
            source_files[filename] = expected_hash

            if first_raw is None:
                first_raw = raw

            print(
                filename,
                "| candles:",
                len(parsed),
                "| fingerprint: PASS"
            )

        reconstructed, provenance = merge_archives(
            batches
        )

        require(
            len(reconstructed) == 1400,
            symbol + ": expected 1,400 distinct candles"
        )

        frozen = dataset_candles(dataset, symbol)

        require(
            set(reconstructed) == set(frozen),
            symbol + ": frozen dataset timestamps differ "
            "from archived source candles"
        )

        differences = []

        for timestamp in sorted(reconstructed):
            source = reconstructed[timestamp]
            stored = frozen[timestamp]

            for field in (
                "open", "high", "low",
                "close", "volume"
            ):
                if source[field] != stored[field]:
                    differences.append(
                        (timestamp, field)
                    )

            if source["session"] != stored["session"]:
                differences.append(
                    (timestamp, "session")
                )

        require(
            not differences,
            symbol + ": "
            + str(len(differences))
            + " archived candle values differ "
            "from frozen Dataset V001"
        )

        by_day = Counter()

        for candle in reconstructed.values():
            dt = datetime.fromisoformat(
                candle["time"]
            ).astimezone(ET)
            by_day[dt.date().isoformat()] += 1

        require(
            all(
                by_day[day] == 78
                for day in full_dates
            ),
            symbol + ": complete-session coverage differs"
        )

        partial_dates = sorted(
            day for day in by_day
            if day not in full_dates
        )

        require(
            len(partial_dates) == 2,
            symbol + ": unexpected partial-session count"
        )

        print("Unique archived candles:", len(reconstructed))
        print("Matching frozen timestamps:", len(frozen))
        print("OHLCV numerical mismatches:", len(differences))
        print("Complete sessions:", len(full_dates))
        print("Partial session dates:", len(partial_dates))
        print("Archive-to-dataset reconstruction: PASS")

        print("\nMALFORMED-ARCHIVE REJECTION TESTS")
        negative_tests(first_raw, symbol)

        report_symbols[symbol] = {
            "archived_candles": len(reconstructed),
            "frozen_dataset_candles": len(frozen),
            "exact_ohlcv_matches": len(frozen),
            "mismatched_values": len(differences),
            "complete_sessions": len(full_dates),
            "partial_session_dates": partial_dates,
            "archived_batches": len(batches),
            "malformed_input_tests_passed": 7
        }

    require(
        report_symbols["SPY"]["archived_candles"] == 1400
        and report_symbols["QQQ"]["archived_candles"] == 1400,
        "Combined source reconstruction incomplete"
    )

    for filename, original_hash in source_files.items():
        require(
            sha((JOURNAL / filename).read_bytes())
            == original_hash,
            "Protected archive changed: " + filename
        )

    require(
        sha(DATASET.read_bytes()) == EXPECTED_DATASET_HASH,
        "Frozen dataset changed during audit"
    )

    output = {
        "version": "WEBULL_ARCHIVE_PARSER_AUDIT_V001",
        "generated_utc": datetime.now(
            timezone.utc
        ).isoformat(),
        "frozen_dataset_sha256": EXPECTED_DATASET_HASH,
        "source_archive_sha256": source_files,
        "symbols": report_symbols,
        "total_archived_candles_reconstructed": 2800,
        "archived_snapshot_to_dataset_reconstruction":
            "VERIFIED",
        "original_http_response_body_verified": False,
        "original_api_to_archive_transformation_verified":
            False,
        "live_api_parser_verified": False,
        "exchange_grade_prices_certified": False,
        "future_sessions_admitted": 0,
        "out_of_sample_evaluation_started": False,
        "trade_signal": False,
        "execution_enabled": False,
        "trade_authorization": False
    }

    os.umask(0o077)

    with OUTPUT.open("x") as handle:
        json.dump(output, handle, indent=2)
        handle.write("\n")

    OUTPUT.chmod(0o600)

    print("\n--- FINAL AUDIT ---")
    print("Eight Webull archive fingerprints: VERIFIED")
    print("Historical candles reconstructed: 2,800")
    print("Exact archive-to-dataset candle comparison: PASS")
    print("Malformed archive tests: 14/14 PASS")
    print("Protected source files: UNCHANGED")
    print("Original HTTP response transformation: NOT VERIFIED")
    print("Live Webull parser: NOT VERIFIED")
    print("Future evaluation started: NO")
    print(
        "Saved: journal/forward_evaluation_v001/"
        "webull_archive_parser_audit_v001.json"
    )

    print("\nWEBULL ARCHIVE PARSER AUDIT PASSED")
    print("WEBULL REQUESTS: NONE")
    print("YAHOO REQUESTS: NONE")
    print("AI REQUESTS: NONE")
    print("ORDER EXECUTION: DISABLED")


if __name__ == "__main__":
    main()
