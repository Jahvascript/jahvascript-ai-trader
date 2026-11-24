"""JahvaScript historical market data collector.
Webull sandbox only. No orders or AI requests.
"""

import json
import logging
import os
from datetime import datetime, timezone, time
from zoneinfo import ZoneInfo
from decimal import Decimal, InvalidOperation
from pathlib import Path

logging.disable(logging.CRITICAL)
os.umask(0o077)

from webull.core.client import ApiClient
from webull.trade.trade_client import TradeClient
from webull.data.data_client import DataClient
from options_scanner import load_config

ROOT = Path(__file__).resolve().parent
OUTPUT = ROOT / "journal" / "history"


def valid_number(value, positive=True):
    try:
        n = Decimal(str(value))
        if not n.is_finite():
            return None
        if positive and n <= 0:
            return None
        return n
    except (InvalidOperation, ValueError, TypeError):
        return None


def extract_bars(payload):
    """Unwrap Webull's nested historical candle response."""

    required = {
        "time", "open", "high",
        "low", "close", "volume"
    }

    if isinstance(payload, dict):
        if required.issubset(payload):
            return [payload]

        for key in ("result", "data", "bars", "items"):
            if key in payload:
                found = extract_bars(payload[key])
                if found is not None:
                    return found

    elif isinstance(payload, list):
        if not payload:
            return []

        if all(
            isinstance(item, dict)
            and required.issubset(item)
            for item in payload
        ):
            return payload

        for item in payload:
            found = extract_bars(item)
            if found:
                return found

    return None


def normalize(bars, timeframe):
    clean = []
    rejected = 0

    for bar in bars:
        if not isinstance(bar, dict):
            rejected += 1
            continue

        values = {
            key: valid_number(
                bar.get(key),
                positive=(key != "volume")
            )
            for key in (
                "open", "high", "low", "close", "volume"
            )
        }

        session = bar.get("trading_session", bar.get("trading_sessions"))

        if (
            any(v is None for v in values.values())
            or values["volume"] < 0
            or values["high"] < values["low"]
            or values["high"] < max(
                values["open"], values["close"]
            )
            or values["low"] > min(
                values["open"], values["close"]
            )
        ):
            rejected += 1
            continue

        if timeframe == "M5" and session != "RTH":
            rejected += 1
            continue

        timestamp = bar.get("time")

        if timestamp is None:
            rejected += 1
            continue

        if timeframe == "M5":
            try:
                dt = datetime.fromisoformat(str(timestamp))

                if dt.tzinfo is None or dt.utcoffset() is None:
                    raise ValueError("Missing timezone")

                eastern = dt.astimezone(
                    ZoneInfo("America/New_York")
                )

                if not (
                    eastern.weekday() < 5
                    and time(9, 30) <= eastern.time() < time(16, 0)
                    and eastern.minute % 5 == 0
                    and eastern.second == 0
                    and eastern.microsecond == 0
                ):
                    raise ValueError("Outside standard RTH M5 grid")

            except (ValueError, TypeError, OverflowError):
                rejected += 1
                continue

        clean.append({
            "time": str(timestamp),
            **{
                key: str(value)
                for key, value in values.items()
            },
            "session": session
        })

    if timeframe == "M5":
        clean.sort(
            key=lambda bar: datetime.fromisoformat(bar["time"])
        )

    return clean, rejected


def main():
    config = load_config()

    if config.get("WEBULL_ENVIRONMENT") != "uat":
        raise RuntimeError("Sandbox-only mode required")

    api = ApiClient(
        config["WEBULL_APP_KEY"],
        config["WEBULL_APP_SECRET"],
        "us"
    )
    api.add_endpoint("us", "api.sandbox.webull.com")

    TradeClient(api)
    data = DataClient(api)

    ignore = (ROOT / ".gitignore").read_text().splitlines()

    if "journal/" not in ignore:
        raise RuntimeError("Journal must be gitignored")

    OUTPUT.mkdir(parents=True, exist_ok=True)

    print("\n=== JAHVASCRIPT HISTORICAL COLLECTOR ===")
    print("Environment: SANDBOX")
    print("Market: SPY / QQQ")
    print("Execution: DISABLED\n")

    for symbol in ("SPY", "QQQ"):
        for timeframe, count in (
            ("M5", 500),
            ("D", 260)
        ):
            label = f"{symbol} {timeframe}"
            print(f"--- {label} ---")

            try:
                response = data.market_data.get_history_bar(
                    symbol=symbol,
                    category="US_STOCK",
                    timespan=timeframe,
                    count=str(count),
                    real_time_required=False,
                    trading_sessions="RTH"
                )

                print("HTTP:", response.status_code)

                if response.status_code != 200:
                    print("BLOCKED: History unavailable\n")
                    continue

                bars = extract_bars(response.json())

                if bars is None:
                    print("BLOCKED: Unknown response format\n")
                    continue

                clean, rejected = normalize(
                    bars, timeframe
                )

                print("Bars returned:", len(bars))
                print("Valid bars:", len(clean))
                print("Rejected bars:", rejected)

                if len(clean) < 50:
                    print("BLOCKED: Insufficient history\n")
                    continue

                result = {
                    "source": "WEBULL_SANDBOX",
                    "symbol": symbol,
                    "timeframe": timeframe,
                    "regular_hours_requested": True,
                    "real_time_required": False,
                    "retrieved_utc": datetime.now(
                        timezone.utc
                    ).isoformat(),
                    "research_only": True,
                    "execution_enabled": False,
                    "bars": clean
                }

                filename = f"{symbol}_{timeframe}.json"
                path = OUTPUT / filename

                path.write_text(
                    json.dumps(result, indent=2) + "\n"
                )
                path.chmod(0o600)

                print("First bar:", clean[0]["time"])
                print("Last bar:", clean[-1]["time"])
                print("Saved:", filename, "\n")

            except Exception as exc:
                print(
                    "Request failed:",
                    type(exc).__name__
                )
                print("Sensitive error details hidden\n")

    print("HISTORICAL COLLECTION COMPLETE")
    print("AI REQUESTS: NONE")
    print("ORDER EXECUTION: DISABLED")


if __name__ == "__main__":
    main()
