"""JahvaScript controlled, resumable historical backfill.
Sandbox data requests only. No AI or order execution.
"""

import json
import os
import shutil
import tempfile
from datetime import datetime, timedelta, time, timezone
from decimal import Decimal
from pathlib import Path
from zoneinfo import ZoneInfo

from webull.core.client import ApiClient
from webull.data.data_client import DataClient
from options_scanner import load_config

ROOT = Path(__file__).resolve().parent
HISTORY = ROOT / "journal/history"
BACKFILL = HISTORY / "backfill"
ET = ZoneInfo("America/New_York")

SYMBOLS = ("SPY", "QQQ")
BATCH_NUMBERS = (2, 3)
COUNT = 400
STEP = timedelta(minutes=5)


def require(ok, message):
    if not ok:
        raise ValueError("STOP: " + message)


def parse_time(value):
    dt = datetime.fromisoformat(str(value))
    require(
        dt.tzinfo is not None
        and dt.utcoffset() is not None,
        "Missing candle timezone"
    )
    return dt.astimezone(timezone.utc)


def extract_bars(value):
    if isinstance(value, list):
        result = []
        for item in value:
            result.extend(extract_bars(item))
        return result

    if isinstance(value, dict):
        fields = {
            "time", "open", "high",
            "low", "close", "volume"
        }

        if fields.issubset(value):
            return [value]

        result = []
        for child in value.values():
            result.extend(extract_bars(child))
        return result

    return []


def validate_bars(bars, label):
    require(bool(bars), label + ": empty dataset")

    cleaned = []

    for bar in bars:
        dt = parse_time(bar["time"])
        local = dt.astimezone(ET)

        session = bar.get(
            "trading_session",
            bar.get("trading_sessions", bar.get("session"))
        )

        require(
            session == "RTH",
            label + ": unexpected trading session"
        )

        require(
            local.weekday() < 5
            and time(9, 30) <= local.time() < time(16, 0)
            and local.minute % 5 == 0
            and local.second == 0
            and local.microsecond == 0,
            label + ": invalid M5 timestamp"
        )

        prices = {
            k: Decimal(str(bar[k]))
            for k in ("open", "high", "low", "close", "volume")
        }

        o, h, l, c, v = (
            prices[k]
            for k in ("open", "high", "low", "close", "volume")
        )

        require(
            all(x.is_finite() for x in prices.values())
            and min(o, h, l, c) > 0
            and l <= min(o, c)
            and h >= max(o, c)
            and v >= 0,
            label + ": invalid OHLCV"
        )

        cleaned.append({
            "time": str(bar["time"]),
            "open": bar["open"],
            "high": bar["high"],
            "low": bar["low"],
            "close": bar["close"],
            "volume": bar["volume"],
            "session": "RTH"
        })

    cleaned.sort(key=lambda b: parse_time(b["time"]))

    times = [parse_time(b["time"]) for b in cleaned]

    require(
        len(times) == len(set(times)),
        label + ": duplicate timestamps"
    )

    for a, b in zip(times, times[1:]):
        require(a < b, label + ": ordering error")

        if a.astimezone(ET).date() == b.astimezone(ET).date():
            require(
                b - a == STEP,
                label + ": missing internal M5 candle"
            )

    return cleaned, times


def verify_connection(older_times, boundary, label):
    require(
        older_times[-1] < boundary,
        label + ": data not older than boundary"
    )

    older_local = older_times[-1].astimezone(ET)
    boundary_local = boundary.astimezone(ET)

    if older_local.date() == boundary_local.date():
        require(
            older_times[-1] + STEP == boundary,
            label + ": five-minute seam gap"
        )
        return "SAME-SESSION CONTINUITY VERIFIED"

    require(
        boundary_local.time() == time(9, 30),
        label + ": unexpected cross-session boundary"
    )

    return "CROSS-SESSION BOUNDARY; CALENDAR UNVERIFIED"


def read_initial_history():
    combined = {}

    for symbol in SYMBOLS:
        original = json.loads(
            (HISTORY / f"{symbol}_M5.json").read_text()
        )
        first = json.loads(
            (
                BACKFILL / f"{symbol}_M5_backfill_001.json"
            ).read_text()
        )

        require(
            original.get("symbol") == symbol
            and original.get("timeframe") == "M5"
            and original.get("execution_enabled") is False,
            symbol + ": unsafe original"
        )
        require(
            first.get("symbol") == symbol
            and first.get("timeframe") == "M5"
            and first.get("execution_enabled") is False,
            symbol + ": unsafe first backfill"
        )

        old_bars, old_times = validate_bars(
            original["bars"], symbol + " original"
        )
        new_bars, new_times = validate_bars(
            first["bars"], symbol + " batch 001"
        )

        require(
            len(old_bars) == 500
            and len(new_bars) == 100,
            symbol + ": unexpected initial counts"
        )

        require(
            new_times[-1] + STEP == old_times[0],
            symbol + ": initial seam mismatch"
        )

        combined[symbol] = new_times + old_times

    require(
        combined["SPY"] == combined["QQQ"],
        "Initial SPY/QQQ timestamps differ"
    )

    return combined


def save_batch(number, collected, boundary):
    final = BACKFILL / f"batch_{number:03d}"

    require(not final.exists(), "Batch destination exists")

    staging = Path(
        tempfile.mkdtemp(
            prefix=".backfill_staging_",
            dir=BACKFILL
        )
    )

    try:
        os.chmod(staging, 0o700)

        for symbol in SYMBOLS:
            payload = {
                "source": "WEBULL_SANDBOX_HISTORICAL",
                "symbol": symbol,
                "timeframe": "M5",
                "batch_number": number,
                "retrieved_utc": datetime.now(
                    timezone.utc
                ).isoformat(),
                "requested_count": COUNT,
                "requested_end_ms": int(
                    boundary.timestamp() * 1000
                ),
                "research_only": True,
                "source_prices_verified": False,
                "real_time_required": False,
                "execution_enabled": False,
                "trade_authorization": False,
                "bars": collected[symbol]
            }

            path = staging / f"{symbol}_M5.json"
            path.write_text(json.dumps(payload, indent=2) + "\n")
            path.chmod(0o600)

        # Publish both symbol files together via one
        # same-filesystem directory rename.
        require(not final.exists(), "Batch destination collision")
        staging.rename(final)

    finally:
        if staging.exists():
            shutil.rmtree(staging)


def load_saved_batch(number, boundary):
    folder = BACKFILL / f"batch_{number:03d}"
    result = {}
    time_sets = {}

    for symbol in SYMBOLS:
        path = folder / f"{symbol}_M5.json"
        require(path.is_file(), "Incomplete saved batch")

        data = json.loads(path.read_text())

        require(
            data.get("symbol") == symbol
            and data.get("timeframe") == "M5"
            and data.get("batch_number") == number
            and data.get("execution_enabled") is False
            and data.get("trade_authorization") is False
            and data.get("requested_end_ms")
                == int(boundary.timestamp() * 1000),
            "Saved batch metadata mismatch"
        )

        bars, times = validate_bars(
            data["bars"], symbol + " saved batch"
        )

        verify_connection(times, boundary, symbol)
        result[symbol] = bars
        time_sets[symbol] = times

    require(
        time_sets["SPY"] == time_sets["QQQ"],
        "Saved batch alignment mismatch"
    )

    return result, time_sets


def main():
    print("\n=== JAHVASCRIPT MULTI-BATCH BACKFILL ===")

    require(
        "journal/" in (
            ROOT / ".gitignore"
        ).read_text().splitlines(),
        "Journal must be gitignored"
    )

    config = load_config()

    require(
        config.get("WEBULL_ENVIRONMENT") == "uat",
        "Sandbox-only environment required"
    )

    history = read_initial_history()

    print("Environment: SANDBOX")
    print("Existing bars per symbol:", len(history["SPY"]))
    print("Batch size:", COUNT)
    print("Planned new batches:", len(BATCH_NUMBERS))
    print("Original datasets: READ ONLY")
    print("Order execution: DISABLED")

    client = None

    for number in BATCH_NUMBERS:
        boundary = history["SPY"][0]
        folder = BACKFILL / f"batch_{number:03d}"

        print(f"\n--- BACKFILL BATCH {number:03d} ---")
        print("Exclusive end:", boundary.isoformat())

        if folder.exists():
            print("Existing batch found; verifying for resume...")
            _, time_sets = load_saved_batch(number, boundary)
            print("Previously saved batch: VERIFIED")

        else:
            if client is None:
                api = ApiClient(
                    config["WEBULL_APP_KEY"],
                    config["WEBULL_APP_SECRET"],
                    "us"
                )
                api.add_endpoint(
                    "us", "api.sandbox.webull.com"
                )
                client = DataClient(api)

            collected = {}
            time_sets = {}

            for symbol in SYMBOLS:
                print("\nSymbol:", symbol)

                response = (
                    client.market_data.get_batch_history_bar(
                        symbols=[symbol],
                        category="US_STOCK",
                        timespan="M5",
                        count=str(COUNT),
                        real_time_required=False,
                        trading_sessions="RTH",
                        end_time=int(boundary.timestamp() * 1000)
                    )
                )

                print("HTTP:", response.status_code)
                require(
                    response.status_code == 200,
                    symbol + ": request failed"
                )

                raw = extract_bars(response.json())

                require(
                    0 < len(raw) <= COUNT,
                    symbol + ": unexpected API count"
                )

                clean, times = validate_bars(
                    raw, symbol + " new batch"
                )

                note = verify_connection(
                    times, boundary, symbol
                )

                collected[symbol] = clean
                time_sets[symbol] = times

                print("Valid candles:", len(clean))
                print("Oldest:", times[0].isoformat())
                print("Newest:", times[-1].isoformat())
                print("Boundary:", note)

            require(
                time_sets["SPY"] == time_sets["QQQ"],
                "New SPY/QQQ timestamps differ"
            )

            save_batch(number, collected, boundary)
            print("Saved batch folder:", folder.relative_to(ROOT))

        for symbol in SYMBOLS:
            earlier = time_sets[symbol]

            require(
                not (set(earlier) & set(history[symbol])),
                symbol + ": timestamp overlap"
            )

            history[symbol] = earlier + history[symbol]

        require(
            history["SPY"] == history["QQQ"],
            "Combined SPY/QQQ alignment failed"
        )

        print(
            "Combined candles per symbol:",
            len(history["SPY"])
        )

    print("\n=== FINAL BACKFILL STATUS ===")
    print("SPY candles:", len(history["SPY"]))
    print("QQQ candles:", len(history["QQQ"]))
    print("Earliest:", history["SPY"][0].isoformat())
    print("Latest:", history["SPY"][-1].isoformat())
    print("Combined timestamp alignment: PASS")
    print("Original data and research reports: UNCHANGED")
    print("Trading-calendar validation: NOT PERFORMED")
    print("Source prices independently verified: NO")
    print("\nMULTI-BATCH BACKFILL PASSED")
    print("AI REQUESTS: NONE")
    print("ORDER EXECUTION: DISABLED")


if __name__ == "__main__":
    main()
