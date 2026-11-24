"""Audit saved Yahoo raw-to-candle reconstruction.

Checks the existing 17 complete historical sessions,
confirms previous comparison percentages, exercises
tampered-response rejection, and inventories potential
Webull source artifacts without exposing their contents.
"""

import copy
import hashlib
import json
import os
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path

import forward_ingestion_v001 as ingestion
from provider_raw_parsers_v001 import parse_yahoo

ROOT = Path(__file__).resolve().parent
JOURNAL = ROOT / "journal"
PRICE = JOURNAL / "price_verification"

DATASET = JOURNAL / "datasets/M5_SPY_QQQ_v001.json"
COMPARISON = PRICE / "yahoo_comparison_v001.json"
OUTPUT = (
    JOURNAL / "forward_evaluation_v001"
    / "yahoo_parser_audit_v001.json"
)

PARSER_FILE = ROOT / "provider_raw_parsers_v001.py"

EXPECTED_HASH = (
    "4c55e6c7961e9210f73f1468b20498fe20db4941cd27b10bd11753336292e3cb"
)

FIELDS = ("open", "high", "low", "close")
TOLERANCE = Decimal("0.02")


def require(ok, reason):
    if not ok:
        raise ValueError(
            "PROVIDER PARSER AUDIT STOPPED: " + reason
        )


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def reject(payload, symbol, dates, description):
    raw = json.dumps(payload).encode()

    try:
        parse_yahoo(raw, symbol, dates)
    except (ValueError, KeyError, TypeError, IndexError):
        print(description + ": REJECTED AS EXPECTED")
        return

    raise ValueError(
        "Invalid provider response accepted: " + description
    )


def test_rejection(raw, symbol, dates):
    original = json.loads(raw)
    stamps = original["chart"]["result"][0]["timestamp"]

    relevant_index = next(
        i for i, stamp in enumerate(stamps)
        if datetime.fromtimestamp(
            stamp, timezone.utc
        ).astimezone(
            __import__("zoneinfo").ZoneInfo(
                "America/New_York"
            )
        ).date().isoformat() in dates
    )

    wrong_symbol = copy.deepcopy(original)
    wrong_symbol["chart"]["result"][0][
        "meta"
    ]["symbol"] = "INVALID"

    reject(
        wrong_symbol, symbol, dates,
        "Wrong instrument"
    )

    duplicate = copy.deepcopy(original)
    duplicate["chart"]["result"][0][
        "timestamp"
    ][relevant_index + 1] = (
        duplicate["chart"]["result"][0][
            "timestamp"
        ][relevant_index]
    )

    reject(
        duplicate, symbol, dates,
        "Duplicate/missing candle timestamp"
    )

    missing = copy.deepcopy(original)
    missing["chart"]["result"][0][
        "indicators"
    ]["quote"][0]["close"][relevant_index] = None

    reject(
        missing, symbol, dates,
        "Missing closing price"
    )

    invalid = copy.deepcopy(original)
    invalid["chart"]["result"][0][
        "indicators"
    ]["quote"][0]["high"][relevant_index] = -1

    reject(
        invalid, symbol, dates,
        "Impossible candle price"
    )


def source_candles(dataset, symbol, days):
    found = {}

    for bar in dataset["symbols"][symbol]["bars"]:
        dt = datetime.fromisoformat(
            bar["time"]
        ).astimezone(timezone.utc)

        local_day = dt.astimezone(
            __import__("zoneinfo").ZoneInfo(
                "America/New_York"
            )
        ).date().isoformat()

        if local_day not in days:
            continue

        key = dt.isoformat()

        require(
            key not in found,
            "Duplicate frozen Webull timestamp"
        )

        found[key] = {
            field: Decimal(str(bar[field]))
            for field in FIELDS
        }

    require(
        len(found) == 1326,
        "Unexpected frozen Webull candle count"
    )

    return found


def find_webull_candidates():
    """Print filenames only; never dump API payloads."""
    matches = []

    for path in JOURNAL.rglob("*"):
        if not path.is_file():
            continue

        relative = path.relative_to(JOURNAL)
        label = relative.as_posix().lower()

        if (
            label.startswith("price_verification/")
            or label.startswith("forward_evaluation_v001/")
            or label.startswith("datasets/")
        ):
            continue

        if path.suffix.lower() not in (
            ".json", ".jsonl", ".ndjson"
        ):
            continue

        if any(
            word in label
            for word in (
                "webull", "backfill",
                "spy", "qqq", "history",
                "m5", "raw"
            )
        ):
            matches.append(relative.as_posix())

    return sorted(matches)


def main():
    print(
        "\n=== JAHVASCRIPT PROVIDER RAW PARSER AUDIT ==="
    )

    require(
        not OUTPUT.exists(),
        "V001 Yahoo parser report already exists"
    )

    before_plan = ingestion.verify_plan()

    require(
        sha(DATASET) == EXPECTED_HASH,
        "Dataset V001 fingerprint changed"
    )

    dataset = json.loads(DATASET.read_text())
    comparison = json.loads(COMPARISON.read_text())

    require(
        comparison.get("dataset_sha256") == EXPECTED_HASH
        and comparison.get("execution_enabled") is False
        and comparison.get("trade_authorization") is False,
        "Historical comparison safety/linkage mismatch"
    )

    dates = sorted(
        dataset["full_standard_session_dates"]
    )

    require(
        len(dates) == 17,
        "Expected 17 previously explored sessions"
    )

    raws = {
        symbol: PRICE / (
            "yahoo_" + symbol.lower()
            + "_m5_raw_v001.json"
        )
        for symbol in ("SPY", "QQQ")
    }

    protected = [
        DATASET, COMPARISON, *raws.values()
    ]

    before = {
        str(path): sha(path)
        for path in protected
    }

    results = {}

    for symbol in ("SPY", "QQQ"):
        path = raws[symbol]

        require(
            sha(path)
            == comparison["raw_provider_responses"][
                symbol
            ]["sha256"],
            symbol + ": Yahoo raw hash mismatch"
        )

        raw = path.read_bytes()
        parsed = parse_yahoo(raw, symbol, dates)
        reference = source_candles(
            dataset, symbol, set(dates)
        )

        flattened = {
            bar["time"]: bar
            for bars in parsed.values()
            for bar in bars
        }

        require(
            set(flattened) == set(reference),
            symbol + ": raw Yahoo timestamps "
            "do not align with frozen Webull candles"
        )

        close_agreement = 0
        ohlc_agreement = 0

        for key, yahoo in flattened.items():
            webull = reference[key]

            differences = {
                field: abs(
                    webull[field] - yahoo[field]
                )
                for field in FIELDS
            }

            close_agreement += (
                differences["close"] <= TOLERANCE
            )

            ohlc_agreement += all(
                diff <= TOLERANCE
                for diff in differences.values()
            )

        total = len(flattened)
        close_pct = round(
            100 * close_agreement / total, 1
        )
        ohlc_pct = round(
            100 * ohlc_agreement / total, 1
        )

        previous = comparison["symbols"][symbol]

        require(
            close_pct == round(
                previous["close_within_2_cents_pct"], 1
            )
            and ohlc_pct == round(
                previous["all_ohlc_within_2_cents_pct"], 1
            ),
            symbol + ": independently parsed "
            "prices differ from the prior comparison"
        )

        print("\n---", symbol, "---")
        print("Saved raw response fingerprint: VERIFIED")
        print("Sessions reconstructed:", len(parsed))
        print("Five-minute RTH candles:", total)
        print("Timestamp alignment: 100.0%")
        print("Close within $0.02:", str(close_pct) + "%")
        print("Entire OHLC within $0.02:", str(ohlc_pct) + "%")
        print("Previous comparison reproduced: PASS")

        test_rejection(raw, symbol, set(dates))

        results[symbol] = {
            "raw_sha256": sha(path),
            "sessions_reconstructed": len(parsed),
            "candles_reconstructed": total,
            "timestamp_alignment": "PASS",
            "close_within_2c_pct": close_pct,
            "all_ohlc_within_2c_pct": ohlc_pct,
            "negative_parser_tests": 4
        }

    candidates = find_webull_candidates()

    print("\n--- WEBULL RAW SOURCE DISCOVERY ---")
    print("Possible archived response files:", len(candidates))

    for path in candidates[:15]:
        print("  ", path)

    if not candidates:
        print(
            "No obvious standalone Webull raw JSON "
            "archive found under journal/"
        )

    print("Webull raw format verified: NO")
    print("Webull parser implemented: NO")

    require(
        all(
            sha(path) == before[str(path)]
            for path in protected
        )
        and ingestion.verify_plan() == before_plan,
        "Existing research sources or plan changed"
    )

    report = {
        "version": "YAHOO_RAW_PARSER_AUDIT_V001",
        "generated_utc": datetime.now(
            timezone.utc
        ).isoformat(),
        "dataset_sha256": EXPECTED_HASH,
        "parser_source_sha256": sha(PARSER_FILE),
        "results": results,
        "webull_raw_candidate_paths": candidates[:30],
        "webull_raw_parser_verified": False,
        "future_real_sessions_admitted": 0,
        "historical_raw_provider_validation": True,
        "exchange_grade_prices_certified": False,
        "official_market_calendar_verified": False,
        "out_of_sample_evaluation_started": False,
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
        all(
            sha(path) == before[str(path)]
            for path in protected
        )
        and ingestion.verify_plan() == before_plan,
        "Protected files changed during audit"
    )

    print("\n--- FINAL RESULT ---")
    print("Yahoo raw-to-OHLCV reconstruction: VERIFIED")
    print("Webull raw-to-OHLCV reconstruction: PENDING")
    print("Frozen research and future manifest: UNCHANGED")
    print("Real future sessions admitted: 0")
    print(
        "Saved: journal/forward_evaluation_v001/"
        "yahoo_parser_audit_v001.json"
    )

    print("\nYAHOO RAW PARSER AUDIT PASSED")
    print("WEBULL REQUESTS: NONE")
    print("YAHOO REQUESTS: NONE")
    print("AI REQUESTS: NONE")
    print("ORDER EXECUTION: DISABLED")


if __name__ == "__main__":
    main()
