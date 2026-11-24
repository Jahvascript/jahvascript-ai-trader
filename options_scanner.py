import logging
from datetime import datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation
from pathlib import Path
from zoneinfo import ZoneInfo

# Prevent SDK errors from logging sensitive request headers
logging.disable(logging.CRITICAL)

from webull.core.client import ApiClient
from webull.data.data_client import DataClient
from webull.trade.trade_client import TradeClient

ROOT = Path(__file__).resolve().parent
EASTERN = ZoneInfo("America/New_York")


def load_config():
    values = {}
    for line in (ROOT / ".env").read_text().splitlines():
        if "=" in line and not line.lstrip().startswith("#"):
            key, value = line.split("=", 1)
            values[key.strip()] = value.strip()

    if values.get("WEBULL_ENVIRONMENT") != "uat":
        raise RuntimeError("Sandbox setting required")

    if not values.get("WEBULL_APP_KEY") or not values.get(
        "WEBULL_APP_SECRET"
    ):
        raise RuntimeError("Sandbox credentials missing")

    return values


def records(payload):
    if isinstance(payload, list):
        return payload

    if isinstance(payload, dict):
        for name in ("data", "result", "contracts", "items"):
            if name in payload:
                result = records(payload[name])
                if result is not None:
                    return result

    return None


def decimal(value):
    try:
        number = Decimal(str(value))
        return number if number.is_finite() else None
    except (InvalidOperation, ValueError, TypeError):
        return None


def next_expiration():
    today = datetime.now(EASTERN).date()
    days = (4 - today.weekday()) % 7

    if days < 3:
        days += 7

    return (today + timedelta(days=days)).isoformat()


def select_samples(contracts, ticker, expiry):
    selected = []

    for kind in ("CALL", "PUT"):
        valid = []

        for contract in contracts:
            if not isinstance(contract, dict):
                continue

            strike = decimal(contract.get("strike_price"))

            if (
                contract.get("underlying_symbol") != ticker
                or contract.get("expiration_date") != expiry
                or contract.get("option_type") != kind
                or contract.get("status") != "LISTING"
                or contract.get("tradable_status") != "OC"
                or contract.get("currency") != "USD"
                or contract.get("def_type") != "STANDARD"
                or contract.get("settlement_method") != "PHYSICAL"
                or decimal(contract.get("multiplier")) != 100
                or strike is None
                or not isinstance(contract.get("symbol"), str)
            ):
                continue

            valid.append(contract)

        valid.sort(key=lambda x: decimal(x["strike_price"]))

        if valid:
            middle = len(valid) // 2
            selected.extend(
                valid[max(0, middle - 1):middle + 1]
            )

    return selected


def quote_age(timestamp):
    try:
        quoted = datetime.fromtimestamp(
            int(timestamp) / 1000,
            timezone.utc
        )
        age = (
            datetime.now(timezone.utc) - quoted
        ).total_seconds() / 60

        if age < -2:
            return "Unverified future timestamp"

        if age > 5:
            return f"{age:.0f} minutes old — STALE"

        return (
            f"{age:.0f} minutes old "
            "(market-data entitlement unverified)"
        )

    except (ValueError, TypeError, OverflowError, OSError):
        return "Unknown timestamp"


def main():
    config = load_config()

    api = ApiClient(
        config["WEBULL_APP_KEY"],
        config["WEBULL_APP_SECRET"],
        "us"
    )
    api.add_endpoint("us", "api.sandbox.webull.com")

    TradeClient(api)  # Initialize sandbox session
    data = DataClient(api)
    expiry = next_expiration()

    print("\n=== JAHVASCRIPT OPTIONS SCANNER ===")
    print("Environment: SANDBOX")
    print("Mode: READ ONLY")
    print("Target expiration:", expiry)

    for ticker in ("SPY", "QQQ"):
        print(f"\n--- {ticker} OPTIONS ---")

        try:
            response = data.instrument.list_option_contracts(
                category="US_OPTION",
                underlying_symbols=ticker,
                status="LISTING",
                start_date=expiry,
                end_date=expiry
            )

            print("Contracts HTTP:", response.status_code)

            if response.status_code != 200:
                print("Contract discovery unavailable")
                continue

            contracts = records(response.json())

            if contracts is None:
                print("BLOCKED: Unknown contract format")
                continue

            print("Contracts returned:", len(contracts))

            samples = select_samples(
                contracts, ticker, expiry
            )

            if not samples:
                print("No verified standard contracts found")
                continue

            symbols = list(dict.fromkeys(
                contract["symbol"] for contract in samples
            ))[:8]

            print("Contracts selected for quote test:",
                  len(symbols))

            response = data.option_market_data.get_option_snapshot(
                symbols=",".join(symbols),
                category="US_OPTION"
            )

            print("Snapshots HTTP:", response.status_code)

            if response.status_code != 200:
                print(
                    "Market-data access unavailable. "
                    "A subscription may be required."
                )
                continue

            quotes = records(response.json())

            if quotes is None:
                print("BLOCKED: Unknown snapshot format")
                continue

            lookup = {
                quote["symbol"]: quote
                for quote in quotes
                if isinstance(quote, dict)
                and isinstance(quote.get("symbol"), str)
            }

            for contract in samples:
                symbol = contract["symbol"]
                quote = lookup.get(symbol)

                if quote is None:
                    print(symbol, "— No quote returned")
                    continue

                bid = decimal(quote.get("bid"))
                ask = decimal(quote.get("ask"))

                valid_prices = (
                    bid is not None
                    and ask is not None
                    and bid > 0
                    and ask >= bid
                )

                print(
                    f"\n{symbol}"
                    f" | {contract['option_type']}"
                    f" | Strike: {contract['strike_price']}"
                )

                print(
                    "Bid:", quote.get("bid", "N/A"),
                    "| Ask:", quote.get("ask", "N/A")
                )

                print(
                    "Volume:", quote.get("volume", "N/A"),
                    "| Open interest:",
                    quote.get("open_interest", "N/A")
                )

                print(
                    "Delta:", quote.get("delta", "N/A"),
                    "| IV:", quote.get("imp_vol", "N/A")
                )

                print(
                    "Quote age:",
                    quote_age(quote.get("quote_time"))
                )

                print(
                    "Price validation:",
                    "PASS" if valid_prices else "BLOCKED"
                )

        except Exception as exc:
            import re

            details = str(exc)

            status = re.search(
                r"HTTP Status:\s*(\d{3})", details
            )
            code = re.search(
                r"\bCode:\s*([A-Za-z0-9_]+)", details
            )

            print("Query stopped:", type(exc).__name__)
            print(
                "HTTP Status:",
                status.group(1) if status else "Unavailable"
            )
            print(
                "Error Code:",
                code.group(1) if code else "Unavailable"
            )
            print("Sensitive request details hidden")

    print("\nOPTIONS DISCOVERY COMPLETE")
    print("NO TRADES EXECUTED")
    print("Samples are NOT trading recommendations")


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        print("SCANNER STOPPED:", type(exc).__name__)
        print("Check local sandbox configuration")
