"""JahvaScript independent-provider discrepancy investigation.

Uses previously saved Yahoo responses and frozen Webull data.
Offline, read-only source inspection; no orders or AI.
"""

import hashlib
import json
import os
import statistics
from collections import Counter, defaultdict
from datetime import datetime, timezone
from decimal import Decimal, ROUND_HALF_UP
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parent
JOURNAL = ROOT / "journal"
DATASET = JOURNAL / "datasets/M5_SPY_QQQ_v001.json"
FOLDER = JOURNAL / "price_verification"
COMPARISON = FOLDER / "yahoo_comparison_v001.json"
OUTPUT = FOLDER / "price_discrepancy_audit_v001.json"

ET = ZoneInfo("America/New_York")
FIELDS = ("open", "high", "low", "close")
CENT = Decimal("0.01")
LIMITS = (
    Decimal("0.001"),
    Decimal("0.01"),
    Decimal("0.02"),
    Decimal("0.05"),
    Decimal("0.10")
)

EXPECTED_HASH = (
    "4c55e6c7961e9210f73f1468b20498fe20db4941cd27b10bd11753336292e3cb"
)


def require(ok, message):
    if not ok:
        raise ValueError("DISCREPANCY AUDIT STOPPED: " + message)


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def timestamp(value):
    dt = datetime.fromisoformat(value)
    require(
        dt.tzinfo is not None and dt.utcoffset() is not None,
        "Missing source timestamp timezone"
    )
    return dt.astimezone(timezone.utc)


def decimals(row):
    result = {
        field: Decimal(str(row[field]))
        for field in (*FIELDS, "volume")
    }

    require(
        all(v.is_finite() for v in result.values())
        and all(result[f] > 0 for f in FIELDS)
        and result["volume"] >= 0,
        "Invalid source OHLCV"
    )

    return result


def load_webull(dataset, symbol, dates):
    bars = {}

    for row in dataset["symbols"][symbol]["bars"]:
        dt = timestamp(row["time"])
        day = dt.astimezone(ET).date().isoformat()

        if day not in dates:
            continue

        key = dt.isoformat()
        require(key not in bars, "Duplicate Webull timestamp")
        bars[key] = decimals(row)

    require(
        len(bars) == 1326,
        symbol + ": unexpected Webull coverage"
    )

    return bars


def load_yahoo(payload, symbol, dates):
    chart = payload["chart"]

    require(
        chart.get("error") is None,
        symbol + ": Yahoo response indicates an error"
    )

    results = chart["result"]
    require(
        len(results) == 1
        and results[0]["meta"]["symbol"].upper() == symbol,
        "Unexpected Yahoo symbol"
    )

    data = results[0]
    quote = data["indicators"]["quote"][0]
    stamps = data["timestamp"]

    require(
        all(
            len(quote[field]) == len(stamps)
            for field in (*FIELDS, "volume")
        ),
        "Yahoo OHLCV length mismatch"
    )

    bars = {}

    for i, unix_time in enumerate(stamps):
        dt = datetime.fromtimestamp(
            unix_time, timezone.utc
        )
        local = dt.astimezone(ET)
        day = local.date().isoformat()

        if (
            day not in dates
            or not (
                (9, 30) <= (local.hour, local.minute)
                < (16, 0)
            )
        ):
            continue

        raw = {
            field: quote[field][i]
            for field in (*FIELDS, "volume")
        }

        if any(value is None for value in raw.values()):
            continue

        key = dt.isoformat()

        require(
            key not in bars,
            "Duplicate Yahoo timestamp"
        )

        bars[key] = decimals(raw)

    require(
        len(bars) == 1326,
        symbol + ": unexpected Yahoo coverage"
    )

    return bars


def cents(value):
    return value.quantize(
        CENT, rounding=ROUND_HALF_UP
    )


def severity(difference):
    for bound in LIMITS:
        if difference <= bound:
            return "<= $" + str(bound)

    return "> $0.10"


def daypart(local):
    hour = local.hour
    minute = local.minute
    total = hour * 60 + minute

    if total < 690:
        return "MORNING"
    if total < 840:
        return "MIDDAY"
    return "AFTERNOON"


def compare(symbol, webull, yahoo, previous):
    require(
        set(webull) == set(yahoo),
        symbol + ": source timelines do not align"
    )

    field_exceedances = Counter()
    severity_counts = Counter()
    days = defaultdict(
        lambda: {
            "bars": 0,
            "ohlc_over_2c": 0,
            "close_over_2c": 0,
            "maximum_ohlc": Decimal(0)
        }
    )
    periods = defaultdict(
        lambda: {"bars": 0, "ohlc_over_2c": 0}
    )

    outliers = []
    close_differences = []
    volume_differences = []
    raw_ohlc_matched = 0
    cent_ohlc_matched = 0
    close_within = 0
    volume_exact = 0

    for key in sorted(webull):
        a = webull[key]
        b = yahoo[key]
        local = timestamp(key).astimezone(ET)
        day = local.date().isoformat()
        part = daypart(local)

        diffs = {
            field: abs(a[field] - b[field])
            for field in FIELDS
        }

        signed_close = b["close"] - a["close"]
        close_differences.append(float(signed_close))

        max_field = max(
            FIELDS,
            key=lambda field: diffs[field]
        )
        maximum = diffs[max_field]

        severity_counts[severity(maximum)] += 1

        for field in FIELDS:
            if diffs[field] > Decimal("0.02"):
                field_exceedances[field] += 1

        raw_good = all(
            diffs[field] <= Decimal("0.02")
            for field in FIELDS
        )

        # This is only a rounding sensitivity check,
        # not a declaration that either quote is correct.
        cents_good = all(
            abs(cents(a[field]) - cents(b[field]))
            <= Decimal("0.02")
            for field in FIELDS
        )

        raw_ohlc_matched += raw_good
        cent_ohlc_matched += cents_good

        close_good = (
            diffs["close"] <= Decimal("0.02")
        )
        close_within += close_good

        days[day]["bars"] += 1
        days[day]["ohlc_over_2c"] += not raw_good
        days[day]["close_over_2c"] += not close_good

        days[day]["maximum_ohlc"] = max(
            days[day]["maximum_ohlc"],
            maximum
        )

        periods[part]["bars"] += 1
        periods[part]["ohlc_over_2c"] += not raw_good

        if a["volume"] == b["volume"]:
            volume_exact += 1

        if a["volume"] > 0:
            volume_differences.append(
                float(
                    100
                    * abs(a["volume"] - b["volume"])
                    / a["volume"]
                )
            )

        if not raw_good:
            outliers.append({
                "utc": key,
                "local_time": local.isoformat(),
                "maximum_field": max_field,
                "maximum_difference": maximum,
                "field_differences": {
                    field: str(diffs[field])
                    for field in FIELDS
                },
                "webull_ohlc": {
                    field: str(a[field])
                    for field in FIELDS
                },
                "yahoo_ohlc": {
                    field: str(b[field])
                    for field in FIELDS
                }
            })

    count = len(webull)

    require(count == 1326, "Bar count changed")

    require(
        round(100 * close_within / count, 1)
        == round(
            previous["close_within_2_cents_pct"], 1
        ),
        symbol + ": prior close comparison disagrees"
    )

    require(
        round(100 * raw_ohlc_matched / count, 1)
        == round(
            previous["all_ohlc_within_2_cents_pct"], 1
        ),
        symbol + ": prior OHLC comparison disagrees"
    )

    outliers.sort(
        key=lambda entry: entry["maximum_difference"],
        reverse=True
    )

    print("\n---", symbol, "---")
    print("Fully aligned candles:", count)
    print(
        "Close within $0.02:",
        f"{100 * close_within / count:.1f}%"
    )
    print(
        "All OHLC within $0.02:",
        f"{100 * raw_ohlc_matched / count:.1f}%"
    )
    print(
        "All OHLC within $0.02 after cent rounding:",
        f"{100 * cent_ohlc_matched / count:.1f}%"
    )
    print(
        "Candles with OHLC discrepancy:",
        len(outliers)
    )

    print("\nFIELDS EXCEEDING $0.02")
    for field in FIELDS:
        print(
            " ", field.upper() + ":",
            field_exceedances[field]
        )

    print("\nDISCREPANCY SEVERITY (MAXIMUM OHLC)")
    for category in (
        "<= $0.001",
        "<= $0.01",
        "<= $0.02",
        "<= $0.05",
        "<= $0.10",
        "> $0.10"
    ):
        print(
            " ", category + ":",
            severity_counts[category]
        )

    print("\nTIME-OF-DAY DISTRIBUTION")
    for part in ("MORNING", "MIDDAY", "AFTERNOON"):
        row = periods[part]
        print(
            " ", part + ":",
            row["ohlc_over_2c"],
            "/",
            row["bars"],
            "candles with discrepancies"
        )

    ranked_days = sorted(
        days.items(),
        key=lambda pair: (
            pair[1]["ohlc_over_2c"],
            pair[1]["maximum_ohlc"]
        ),
        reverse=True
    )

    print("\nTOP FIVE DATES BY DISCREPANCY COUNT")
    for day, values in ranked_days[:5]:
        print(
            " ", day + ":",
            values["ohlc_over_2c"],
            "candles; largest $",
            values["maximum_ohlc"]
        )

    print("\nFIVE LARGEST INDIVIDUAL DISCREPANCIES")
    for row in outliers[:5]:
        print(
            " ", row["local_time"],
            "|", row["maximum_field"].upper(),
            "| difference $", row["maximum_difference"]
        )

    median_volume = (
        statistics.median(volume_differences)
        if volume_differences else None
    )

    close_bias = statistics.mean(
        close_differences
    )

    print("\nMean signed Yahoo-Webull close difference: $",
          f"{close_bias:.6f}")
    print(
        "Median absolute volume difference:",
        f"{median_volume:.3f}%" if median_volume is not None
        else "N/A"
    )
    print("Exactly matching volume bars:", volume_exact)

    return {
        "aligned_candles": count,
        "close_within_2c": close_within,
        "all_ohlc_within_2c": raw_ohlc_matched,
        "all_ohlc_within_2c_after_cent_rounding":
            cent_ohlc_matched,
        "ohlc_discrepancy_count": len(outliers),
        "field_exceedances": dict(field_exceedances),
        "severity_distribution": dict(severity_counts),
        "by_day": {
            day: {
                **{
                    key: value
                    for key, value in details.items()
                    if key != "maximum_ohlc"
                },
                "maximum_ohlc": str(
                    details["maximum_ohlc"]
                )
            }
            for day, details in sorted(days.items())
        },
        "by_daypart": dict(periods),
        "mean_signed_close_difference": close_bias,
        "median_volume_difference_pct": median_volume,
        "exact_volume_matches": volume_exact,
        "largest_discrepancies": [
            {
                **{
                    key: value
                    for key, value in item.items()
                    if key != "maximum_difference"
                },
                "maximum_difference": str(
                    item["maximum_difference"]
                )
            }
            for item in outliers[:20]
        ]
    }


def main():
    print(
        "\n=== JAHVASCRIPT PRICE DISCREPANCY AUDIT ==="
    )

    require(
        not OUTPUT.exists(),
        "V001 discrepancy report already exists"
    )

    require(
        "journal/" in (
            ROOT / ".gitignore"
        ).read_text().splitlines(),
        "Research journal must be gitignored"
    )

    require(
        digest(DATASET) == EXPECTED_HASH,
        "Protected dataset fingerprint mismatch"
    )

    comparison = json.loads(COMPARISON.read_text())

    require(
        comparison.get("dataset_sha256") == EXPECTED_HASH
        and comparison.get("execution_enabled") is False
        and comparison.get("trade_authorization") is False,
        "Prior comparison configuration mismatch"
    )

    dataset = json.loads(DATASET.read_text())
    dates = set(
        dataset["full_standard_session_dates"]
    )

    paths = [
        DATASET,
        COMPARISON,
        FOLDER / "yahoo_spy_m5_raw_v001.json",
        FOLDER / "yahoo_qqq_m5_raw_v001.json"
    ]

    before = {
        str(path): digest(path)
        for path in paths
    }

    results = {}

    for symbol in ("SPY", "QQQ"):
        raw_path = (
            FOLDER / f"yahoo_{symbol.lower()}_m5_raw_v001.json"
        )

        require(
            digest(raw_path)
            == comparison["raw_provider_responses"][
                symbol
            ]["sha256"],
            symbol + ": Yahoo source fingerprint mismatch"
        )

        yahoo = load_yahoo(
            json.loads(raw_path.read_text()),
            symbol,
            dates
        )

        webull = load_webull(
            dataset, symbol, dates
        )

        results[symbol] = compare(
            symbol,
            webull,
            yahoo,
            comparison["symbols"][symbol]
        )

    require(
        all(digest(path) == before[str(path)] for path in paths),
        "Historical source changed during audit"
    )

    report = {
        "version": "PRICE_DISCREPANCY_AUDIT_V001",
        "created_utc": datetime.now(
            timezone.utc
        ).isoformat(),
        "dataset_sha256": EXPECTED_HASH,
        "comparison_sha256": before[str(COMPARISON)],
        "source_sha256": {
            path.relative_to(ROOT).as_posix(): value
            for path, value in (
                (path, before[str(path)]) for path in paths
            )
        },
        "price_tolerance_usd": "0.02",
        "cent_rounding_is_sensitivity_only": True,
        "symbols": results,
        "price_source_accuracy_established": False,
        "breakout_outcome_sensitivity_tested": False,
        "out_of_sample_validation": False,
        "historical_research_only": True,
        "trade_signal": False,
        "execution_enabled": False,
        "trade_authorization": False
    }

    os.umask(0o077)

    with OUTPUT.open("x") as file:
        json.dump(report, file, indent=2)
        file.write("\n")

    OUTPUT.chmod(0o600)

    print("\n--- FINAL AUDIT ---")
    print("Provider timestamps independently aligned: PASS")
    print("Per-field OHLC discrepancies measured: PASS")
    print("Precision sensitivity calculated: PASS")
    print("Original Webull/Yahoo source files: UNCHANGED")
    print("Market-structure outcome sensitivity: NOT YET TESTED")
    print("Exchange-certified price accuracy: NOT ESTABLISHED")
    print("Saved: journal/price_verification/price_discrepancy_audit_v001.json")

    print("\nPRICE DISCREPANCY AUDIT PASSED")
    print("WEBULL REQUESTS: NONE")
    print("YAHOO REQUESTS: NONE")
    print("AI REQUESTS: NONE")
    print("ORDER EXECUTION: DISABLED")


if __name__ == "__main__":
    main()
