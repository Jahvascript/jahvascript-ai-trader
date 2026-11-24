"""JahvaScript controlled historical backfill.
Sandbox market-data requests only. No orders or AI.
Original audited history is never overwritten.
"""

import json
import os
import tempfile
from datetime import datetime, timedelta, timezone, time
from decimal import Decimal, InvalidOperation
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
COUNT = 100
INTERVAL = timedelta(minutes=5)


def require(condition, message):
    if not condition:
        raise ValueError("STOP: " + message)


def parse_time(value):
    dt = datetime.fromisoformat(str(value))
    require(
        dt.tzinfo is not None
        and dt.utcoffset() is not None,
        "Timestamp missing timezone"
    )
    return dt.astimezone(timezone.utc)


def extract_bars(value):
    found = []

    if isinstance(value, dict):
        required = {
            "time", "open", "high",
            "low", "close", "volume"
        }

        if required.issubset(value):
            found.append(value)
        else:
            for child in value.values():
                found.extend(extract_bars(child))

    elif isinstance(value, list):
        for child in value:
            found.extend(extract_bars(child))

    return found


def validate_prices(bar):
    try:
        values = {
            key: Decimal(str(bar[key]))
            for key in (
                "open", "high", "low",
                "close", "volume"
            )
        }
    except (
        KeyError, TypeError,
        ValueError, InvalidOperation
    ):
        raise ValueError("Invalid OHLCV fields")

    o = values["open"]
    h = values["high"]
    l = values["low"]
    c = values["close"]
    v = values["volume"]

    require(
        all(x.is_finite() for x in values.values())
        and min(o, h, l, c) > 0
        and l <= min(o, c)
        and h >= max(o, c)
        and v >= 0,
        "Invalid OHLCV values"
    )


def validate_backfill(raw_bars, boundary, original_times):
    require(
        0 < len(raw_bars) <= COUNT,
        "Unexpected returned candle count"
    )

    clean = []

    for bar in raw_bars:
        dt = parse_time(bar["time"])
        eastern = dt.astimezone(ET)

        session = bar.get(
            "trading_session",
            bar.get("trading_sessions", bar.get("session"))
        )

        require(session == "RTH", "Non-RTH candle")
        require(
            eastern.weekday() < 5
            and time(9, 30) <= eastern.time() < time(16, 0)
            and eastern.minute % 5 == 0
            and eastern.second == 0
            and eastern.microsecond == 0,
            "Invalid M5 session timestamp"
        )

        require(dt < boundary, "Boundary not respected")
        require(
            dt not in original_times,
            "Backfill overlaps existing history"
        )

        validate_prices(bar)

        clean.append({
            "time": bar["time"],
            "open": bar["open"],
            "high": bar["high"],
            "low": bar["low"],
            "close": bar["close"],
            "volume": bar["volume"],
            "session": "RTH"
        })

    clean.sort(key=lambda bar: parse_time(bar["time"]))

    times = [parse_time(bar["time"]) for bar in clean]

    require(
        len(times) == len(set(times)),
        "Duplicate backfill timestamps"
    )

    # This explicitly tests the suspected exclusive
    # end-time behavior, preventing a missing boundary bar.
    require(
        times[-1] == boundary - INTERVAL,
        "Backfill does not connect to original dataset"
    )

    # Audit gaps only within the same trading date.
    for earlier, later in zip(times, times[1:]):
        same_day = (
            earlier.astimezone(ET).date()
            == later.astimezone(ET).date()
        )

        if same_day:
            require(
                later - earlier == INTERVAL,
                "Missing internal five-minute interval"
            )

    return clean, times


def write_new_json(path, payload):
    """Create a new file without replacing an existing one."""
    require(not path.exists(), "Output already exists")

    content = json.dumps(payload, indent=2) + "\n"
    temp = None

    try:
        fd, temp = tempfile.mkstemp(
            prefix=".backfill-",
            dir=str(path.parent)
        )

        with os.fdopen(fd, "w") as handle:
            handle.write(content)

        os.chmod(temp, 0o600)

        # Exclusive creation: never overwrite an existing file.
        with open(path, "x") as target:
            target.write(content)

        os.chmod(path, 0o600)
    finally:
        if temp and os.path.exists(temp):
            os.unlink(temp)


def main():
    print("\n=== JAHVASCRIPT SAFE HISTORICAL BACKFILL ===")

    config = load_config()

    require(
        config.get("WEBULL_ENVIRONMENT") == "uat",
        "Sandbox environment required"
    )
    require(
        "journal/" in (ROOT / ".gitignore").read_text().splitlines(),
        "Journal must be gitignored"
    )

    originals = {}
    boundaries = {}

    # Check BOTH symbols and output destinations
    # before making any API requests.
    for symbol in SYMBOLS:
        path = HISTORY / f"{symbol}_M5.json"
        output = BACKFILL / f"{symbol}_M5_backfill_001.json"

        require(path.is_file(), symbol + " history missing")
        require(not output.exists(), symbol + " output exists")

        source = json.loads(path.read_text())

        require(
            source.get("symbol") == symbol
            and source.get("timeframe") == "M5"
            and source.get("execution_enabled") is False,
            symbol + " history configuration invalid"
        )

        bars = source["bars"]
        require(len(bars) >= 55, "Insufficient original bars")

        times = [parse_time(b["time"]) for b in bars]

        require(
            times == sorted(times)
            and len(times) == len(set(times)),
            symbol + " original timestamps invalid"
        )

        originals[symbol] = set(times)
        boundaries[symbol] = times[0]

    require(
        boundaries["SPY"] == boundaries["QQQ"],
        "SPY/QQQ historical boundaries differ"
    )

    boundary = boundaries["SPY"]
    end_ms = int(boundary.timestamp() * 1000)

    print("Environment: SANDBOX")
    print("Symbols: SPY / QQQ")
    print("Timeframe: M5")
    print("Requested candles per symbol:", COUNT)
    print("Original oldest:", boundary.isoformat())
    print("Requested exclusive end:", boundary.isoformat())
    print("Expected newest backfill:", (
        boundary - INTERVAL
    ).isoformat())
    print("Original files: READ ONLY")
    print("Order execution: DISABLED")

    api = ApiClient(
        config["WEBULL_APP_KEY"],
        config["WEBULL_APP_SECRET"],
        "us"
    )
    api.add_endpoint("us", "api.sandbox.webull.com")

    client = DataClient(api)
    collected = {}
    timestamp_sets = {}

    for symbol in SYMBOLS:
        print("\n---", symbol, "BACKFILL ---")

        try:
            response = client.market_data.get_batch_history_bar(
                symbols=[symbol],
                category="US_STOCK",
                timespan="M5",
                count=str(COUNT),
                real_time_required=False,
                trading_sessions="RTH",
                end_time=end_ms
            )
        except Exception as exc:
            raise RuntimeError(
                symbol + " request failed: "
                + type(exc).__name__
            ) from None

        print("HTTP:", response.status_code)

        require(
            response.status_code == 200,
            symbol + " historical request unsuccessful"
        )

        raw = extract_bars(response.json())
        print("Bars returned:", len(raw))

        clean, times = validate_backfill(
            raw, boundary, originals[symbol]
        )

        collected[symbol] = clean
        timestamp_sets[symbol] = set(times)

        print("Validated candles:", len(clean))
        print("Earliest:", times[0].isoformat())
        print("Latest:", times[-1].isoformat())
        print("Boundary continuity: PASS")
        print("Duplicates: 0")
        print("Internal session gaps: 0")

    require(
        timestamp_sets["SPY"] == timestamp_sets["QQQ"],
        "SPY/QQQ backfill timestamps do not align"
    )

    BACKFILL.mkdir(parents=True, exist_ok=True)
    os.chmod(BACKFILL, 0o700)

    for symbol in SYMBOLS:
        path = BACKFILL / f"{symbol}_M5_backfill_001.json"

        payload = {
            "source": "WEBULL_SANDBOX_HISTORICAL_BACKFILL",
            "symbol": symbol,
            "timeframe": "M5",
            "retrieved_utc": datetime.now(
                timezone.utc
            ).isoformat(),
            "requested_count": COUNT,
            "requested_end_ms": end_ms,
            "original_oldest_utc": boundary.isoformat(),
            "research_only": True,
            "source_prices_verified": False,
            "real_time_required": False,
            "execution_enabled": False,
            "trade_authorization": False,
            "bars": collected[symbol]
        }

        write_new_json(path, payload)
        print("Saved:", path.relative_to(ROOT))

    print("\nSPY/QQQ timestamp alignment: PASS")
    print("Original historical files: UNCHANGED")
    print("Existing indicator/trap reports: UNCHANGED")
    print("New backfill batches: VALIDATED")
    print("Source prices independently verified: NO")
    print("\nHISTORICAL BACKFILL BATCH PASSED")
    print("AI REQUESTS: NONE")
    print("ORDER EXECUTION: DISABLED")


if __name__ == "__main__":
    main()
