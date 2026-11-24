"""JahvaScript independent-provider historical price comparison.

Reads immutable Dataset V001; requests Yahoo Finance M5 bars.
Never adjusts source candles or enables trading.
"""

import hashlib
import json
import math
import os
import statistics
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parent
JOURNAL = ROOT / "journal"
DATASET = JOURNAL / "datasets/M5_SPY_QQQ_v001.json"
OUTDIR = JOURNAL / "price_verification"
REPORT = OUTDIR / "yahoo_comparison_v001.json"

ET = ZoneInfo("America/New_York")
PRICE_TOLERANCE = Decimal("0.02")
EXPECTED_HASH = (
    "4c55e6c7961e9210f73f1468b20498fe20db4941cd27b10bd11753336292e3cb"
)


def require(condition, message):
    if not condition:
        raise ValueError("PRICE AUDIT STOPPED: " + message)


def sha(blob):
    return hashlib.sha256(blob).hexdigest()


def utc(value):
    dt = datetime.fromisoformat(value)
    require(
        dt.tzinfo is not None
        and dt.utcoffset() is not None,
        "Source timestamp lacks timezone"
    )
    return dt.astimezone(timezone.utc)


def fetch_yahoo(symbol, start_day, end_exclusive):
    begin = datetime.combine(
        start_day,
        datetime.min.time(),
        tzinfo=ET
    )
    end = datetime.combine(
        end_exclusive,
        datetime.min.time(),
        tzinfo=ET
    )

    params = urllib.parse.urlencode({
        "period1": int(begin.timestamp()),
        "period2": int(end.timestamp()),
        "interval": "5m",
        "includePrePost": "false",
        "events": "false"
    })

    url = (
        "https://query1.finance.yahoo.com/v8/finance/chart/"
        + symbol + "?" + params
    )

    request = urllib.request.Request(
        url,
        headers={
            "User-Agent": "Mozilla/5.0",
            "Accept": "application/json"
        }
    )

    try:
        with urllib.request.urlopen(
            request, timeout=25
        ) as response:
            raw = response.read(8_000_001)

            require(
                len(raw) <= 8_000_000,
                "Yahoo response unexpectedly large"
            )

    except (
        urllib.error.URLError,
        TimeoutError,
        OSError
    ) as error:
        raise RuntimeError(
            "Yahoo request failed for " + symbol
            + ": " + str(error)
            + ". No source prices have been verified."
        ) from error

    payload = json.loads(raw)
    chart = payload.get("chart") or {}

    require(
        not chart.get("error"),
        symbol + ": provider returned an error: "
        + str(chart.get("error"))
    )

    results = chart.get("result")

    require(
        isinstance(results, list) and len(results) == 1,
        symbol + ": missing provider chart result"
    )

    result = results[0]
    meta = result.get("meta") or {}

    require(
        meta.get("symbol", "").upper() == symbol
        and meta.get("exchangeTimezoneName")
            == "America/New_York",
        symbol + ": unexpected Yahoo instrument/timezone"
    )

    times = result.get("timestamp") or []
    quotes = (
        (result.get("indicators") or {})
        .get("quote") or []
    )

    require(
        len(quotes) == 1 and len(times) > 0,
        symbol + ": no usable five-minute candles returned"
    )

    quote = quotes[0]

    require(
        all(
            isinstance(quote.get(field), list)
            and len(quote[field]) == len(times)
            for field in (
                "open", "high", "low", "close", "volume"
            )
        ),
        symbol + ": inconsistent Yahoo OHLCV arrays"
    )

    parsed = {}
    excluded = 0

    for i, stamp in enumerate(times):
        dt = datetime.fromtimestamp(
            stamp, tz=timezone.utc
        )
        local = dt.astimezone(ET)

        if (
            local.date() < start_day
            or local.date() >= end_exclusive
            or not (
                (9, 30) <= (local.hour, local.minute)
                < (16, 0)
            )
        ):
            excluded += 1
            continue

        values = {
            key: quote[key][i]
            for key in (
                "open", "high", "low",
                "close", "volume"
            )
        }

        if any(value is None for value in values.values()):
            excluded += 1
            continue

        numbers = {
            key: Decimal(str(value))
            for key, value in values.items()
        }

        require(
            all(v.is_finite() for v in numbers.values())
            and min(
                numbers[k]
                for k in (
                    "open", "high", "low", "close"
                )
            ) > 0
            and numbers["volume"] >= 0,
            symbol + ": invalid provider OHLCV"
        )

        key = dt.isoformat()

        require(
            key not in parsed,
            symbol + ": duplicate provider timestamp"
        )

        parsed[key] = numbers

    require(
        parsed,
        symbol + ": no regular-session provider data"
    )

    return raw, parsed, excluded


def source_bars(dataset, symbol, full_dates):
    rows = {}

    for bar in dataset["symbols"][symbol]["bars"]:
        dt = utc(bar["time"])
        day = dt.astimezone(ET).date().isoformat()

        if day not in full_dates:
            continue

        key = dt.isoformat()
        require(
            key not in rows,
            symbol + ": duplicate source timestamp"
        )

        rows[key] = {
            field: Decimal(str(bar[field]))
            for field in (
                "open", "high", "low",
                "close", "volume"
            )
        }

    require(
        len(rows) == 17 * 78,
        symbol + ": unexpected source candle count"
    )

    return rows


def compare(symbol, source, independent, full_dates):
    source_keys = set(source)
    other_keys = set(independent)
    common = sorted(source_keys & other_keys)

    require(
        common,
        symbol + ": no aligned timestamps"
    )

    unmatched_source = sorted(source_keys - other_keys)
    unmatched_provider = sorted(other_keys - source_keys)

    good_ohlc = 0
    close_good = 0
    exact_volume = 0
    relative_volume_differences = []
    largest_close = Decimal(0)
    examples = []

    for timestamp in common:
        a = source[timestamp]
        b = independent[timestamp]

        differences = {
            field: abs(a[field] - b[field])
            for field in (
                "open", "high", "low", "close"
            )
        }

        if all(
            diff <= PRICE_TOLERANCE
            for diff in differences.values()
        ):
            good_ohlc += 1
        elif len(examples) < 6:
            examples.append({
                "utc": timestamp,
                "webull_close": str(a["close"]),
                "yahoo_close": str(b["close"]),
                "close_difference": str(
                    differences["close"]
                ),
                "maximum_ohlc_difference": str(
                    max(differences.values())
                )
            })

        if differences["close"] <= PRICE_TOLERANCE:
            close_good += 1

        largest_close = max(
            largest_close,
            differences["close"]
        )

        if a["volume"] == b["volume"]:
            exact_volume += 1

        if a["volume"] > 0:
            relative_volume_differences.append(
                float(
                    abs(a["volume"] - b["volume"])
                    / a["volume"]
                )
            )

    aligned_percent = (
        100 * len(common) / len(source)
    )

    ohlc_percent = (
        100 * good_ohlc / len(common)
    )

    close_percent = (
        100 * close_good / len(common)
    )

    # This is a conservative comparative label,
    # not exchange-grade data certification.
    consistent = (
        aligned_percent >= 98
        and ohlc_percent >= 98
    )

    print("\n---", symbol, "---")
    print("Webull full-session candles:", len(source))
    print("Yahoo RTH candles:", len(independent))
    print("Timestamp matches:", len(common))
    print(
        "Source timestamp coverage:",
        f"{aligned_percent:.1f}%"
    )
    print("Missing from Yahoo:", len(unmatched_source))
    print("Extra Yahoo timestamps:", len(unmatched_provider))
    print(
        "Close prices within $0.02:",
        f"{close_percent:.1f}%"
    )
    print(
        "Entire OHLC within $0.02:",
        f"{ohlc_percent:.1f}%"
    )
    print("Largest close difference: $", largest_close)
    print("Exactly matching volume bars:", exact_volume)

    volume_median = (
        statistics.median(relative_volume_differences)
        if relative_volume_differences
        else None
    )

    if volume_median is not None:
        print(
            "Median relative volume difference:",
            f"{100 * volume_median:.2f}%"
        )

    print(
        "Second-provider consistency:",
        "CONSISTENT" if consistent
        else "REVIEW_REQUIRED"
    )

    if examples:
        print("Sample price discrepancies:")
        for example in examples[:3]:
            print(
                " ",
                example["utc"],
                "Webull:",
                example["webull_close"],
                "Yahoo:",
                example["yahoo_close"]
            )

    return {
        "webull_bars": len(source),
        "yahoo_bars": len(independent),
        "aligned_bars": len(common),
        "timestamp_coverage_pct": round(
            aligned_percent, 2
        ),
        "missing_yahoo_count": len(unmatched_source),
        "extra_yahoo_count": len(unmatched_provider),
        "missing_yahoo_examples": unmatched_source[:8],
        "extra_yahoo_examples": unmatched_provider[:8],
        "close_within_2_cents_pct": round(
            close_percent, 2
        ),
        "all_ohlc_within_2_cents_pct": round(
            ohlc_percent, 2
        ),
        "largest_close_difference": str(largest_close),
        "exact_volume_matches": exact_volume,
        "median_relative_volume_difference_pct": (
            round(100 * volume_median, 2)
            if volume_median is not None else None
        ),
        "price_discrepancy_examples": examples,
        "status": (
            "CONSISTENT_WITH_SECOND_PROVIDER"
            if consistent
            else "DISCREPANCIES_REQUIRE_REVIEW"
        )
    }


def main():
    print(
        "\n=== JAHVASCRIPT INDEPENDENT PRICE COMPARISON ==="
    )

    require(
        "journal/" in (
            ROOT / ".gitignore"
        ).read_text().splitlines(),
        "Journal must be gitignored"
    )

    require(
        not REPORT.exists(),
        "V001 verification report already exists; "
        "refusing overwrite"
    )

    before = DATASET.read_bytes()
    fingerprint = sha(before)

    require(
        fingerprint == EXPECTED_HASH,
        "Protected Dataset V001 fingerprint mismatch"
    )

    dataset = json.loads(before)

    require(
        dataset.get("historical_research_only") is True
        and dataset.get("execution_enabled") is False
        and dataset.get("trade_authorization") is False,
        "Unsafe dataset configuration"
    )

    full_dates = set(
        dataset["full_standard_session_dates"]
    )

    require(
        len(full_dates) == 17,
        "Unexpected number of complete sessions"
    )

    start_day = datetime.strptime(
        min(full_dates), "%Y-%m-%d"
    ).date()

    end_exclusive = (
        datetime.strptime(
            max(full_dates), "%Y-%m-%d"
        ).date()
        + timedelta(days=1)
    )

    print("Independent source: Yahoo Finance")
    print("Interval: 5 minutes")
    print("Sessions: US regular trading hours")
    print("Start:", start_day)
    print("Exclusive end:", end_exclusive)
    print("OHLC tolerance: $0.02")
    print("Market-data requests planned: 2")
    print("Webull requests: NONE")

    acquired = {}

    # Fetch both before writing anything. A provider
    # failure must never produce a success report.
    for symbol in ("SPY", "QQQ"):
        print("Requesting", symbol, "...", flush=True)
        acquired[symbol] = fetch_yahoo(
            symbol, start_day, end_exclusive
        )

    comparisons = {}
    payloads = {}

    for symbol in ("SPY", "QQQ"):
        raw, independent, excluded = acquired[symbol]
        source = source_bars(
            dataset, symbol, full_dates
        )

        comparisons[symbol] = compare(
            symbol, source, independent, full_dates
        )

        payloads[symbol] = {
            "sha256": sha(raw),
            "bytes": len(raw),
            "excluded_provider_rows": excluded
        }

    require(
        sha(DATASET.read_bytes()) == fingerprint,
        "Protected dataset changed during comparison"
    )

    OUTDIR.mkdir(parents=True, exist_ok=True)
    os.umask(0o077)

    saved_paths = []

    for symbol in ("SPY", "QQQ"):
        path = OUTDIR / (
            "yahoo_" + symbol.lower()
            + "_m5_raw_v001.json"
        )

        with path.open("xb") as file:
            file.write(acquired[symbol][0])

        path.chmod(0o600)
        saved_paths.append(path)

    result = {
        "version": "INDEPENDENT_PRICE_COMPARISON_V001",
        "provider": "Yahoo Finance chart history",
        "provider_is_independent_of_webull_request": True,
        "retrieved_utc": datetime.now(
            timezone.utc
        ).isoformat(),
        "interval": "5m",
        "regular_session_only": True,
        "auto_adjustment_performed_by_this_script": False,
        "corporate_action_and_vendor_conventions_reviewed": False,
        "dataset_sha256": fingerprint,
        "price_tolerance_usd": str(PRICE_TOLERANCE),
        "symbols": comparisons,
        "raw_provider_responses": payloads,
        "any_discrepancies_require_review": any(
            item["status"] != "CONSISTENT_WITH_SECOND_PROVIDER"
            for item in comparisons.values()
        ),
        "independent_exchange_grade_verified": False,
        "out_of_sample_validation": False,
        "predictive_trading_edge_established": False,
        "historical_research_only": True,
        "trade_signal": False,
        "execution_enabled": False,
        "trade_authorization": False
    }

    with REPORT.open("x") as file:
        json.dump(result, file, indent=2)
        file.write("\n")

    REPORT.chmod(0o600)
    saved_paths.append(REPORT)

    require(
        sha(DATASET.read_bytes()) == fingerprint,
        "Protected dataset changed after writing results"
    )

    print("\n--- FINAL SOURCE-COMPARISON RESULT ---")
    for symbol in ("SPY", "QQQ"):
        print(
            symbol + ":",
            comparisons[symbol]["status"]
        )

    print("Second-provider data saved:", len(saved_paths))
    print("Original Dataset V001: UNCHANGED")
    print("Exchange-grade price verification: NOT ESTABLISHED")
    print("Untouched out-of-sample validation: NO")
    print("Saved: journal/price_verification/yahoo_comparison_v001.json")
    print("\nSECOND-PROVIDER COMPARISON COMPLETED")
    print("WEBULL REQUESTS: NONE")
    print("YAHOO HISTORICAL DATA REQUESTS: 2")
    print("AI REQUESTS: NONE")
    print("ORDER EXECUTION: DISABLED")


if __name__ == "__main__":
    main()
