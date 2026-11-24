import logging
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation

logging.disable(logging.CRITICAL)

from webull.core.client import ApiClient
from webull.trade.trade_client import TradeClient
from webull.data.data_client import DataClient

from options_scanner import (
    load_config,
    records,
    select_samples,
    next_expiration
)

MAX_RESEARCH_AGE_HOURS = 96


def number(value):
    if value is None or isinstance(value, bool):
        return None
    try:
        n = Decimal(str(value))
        return n if n.is_finite() else None
    except (InvalidOperation, TypeError, ValueError):
        return None


def assess_quote(quote):
    blockers = []
    warnings = []
    spread_pct = None
    age_minutes = None

    bid = number(quote.get("bid"))
    ask = number(quote.get("ask"))
    volume = number(quote.get("volume"))
    interest = number(quote.get("open_interest"))

    if bid is None or ask is None or bid <= 0 or ask < bid:
        blockers.append("Invalid bid/ask")
    else:
        midpoint = (bid + ask) / 2
        spread_pct = (ask - bid) / midpoint * 100

        if spread_pct > 3:
            blockers.append("Spread exceeds 3%")

    if volume is None or volume < 100:
        blockers.append("Volume below 100 or missing")

    if interest is None or interest < 100:
        blockers.append("Open interest below 100 or missing")

    try:
        timestamp = quote["quote_time"]

        if isinstance(timestamp, bool):
            raise ValueError("Invalid timestamp")

        quote_time = datetime.fromtimestamp(
            int(timestamp) / 1000,
            timezone.utc
        )

        age_minutes = (
            datetime.now(timezone.utc) - quote_time
        ).total_seconds() / 60

        if age_minutes < -1:
            blockers.append("Quote timestamp is in the future")
        elif age_minutes > MAX_RESEARCH_AGE_HOURS * 60:
            blockers.append("Quote too old for research screen")
        elif age_minutes > 2:
            warnings.append("Delayed/stale quote — research only")

    except (KeyError, ValueError, TypeError,
            OverflowError, OSError):
        blockers.append("Missing or invalid quote timestamp")

    warnings.append(
        "Sandbox quote: real-time entitlement unverified"
    )

    return {
        "research_eligible": len(blockers) == 0,
        "spread_pct": spread_pct,
        "age_minutes": age_minutes,
        "blockers": blockers,
        "warnings": warnings
    }


def main():
    config = load_config()

    if config.get("WEBULL_ENVIRONMENT") != "uat":
        raise RuntimeError("Sandbox required")

    api = ApiClient(
        config["WEBULL_APP_KEY"],
        config["WEBULL_APP_SECRET"],
        "us"
    )
    api.add_endpoint("us", "api.sandbox.webull.com")

    TradeClient(api)
    data = DataClient(api)

    expiry = next_expiration()

    print("\n=== JAHVASCRIPT RESEARCH SCANNER ===")
    print("Environment: SANDBOX")
    print("Mode: DELAYED DATA RESEARCH")
    print("Order execution: DISABLED")
    print("Expiration:", expiry)

    for ticker in ("SPY", "QQQ"):
        print(f"\n--- {ticker} ---")

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
                print("Contract request failed")
                continue

            payload = response.json()
            contracts = records(payload)

            if not isinstance(contracts, list):
                print("Unknown contract response")
                continue

            if isinstance(payload, dict) and payload.get(
                "pagination_key"
            ):
                print("WARNING: Additional contract pages exist")

            samples = select_samples(
                contracts, ticker, expiry
            )

            if not samples:
                print("No matching contracts found")
                continue

            symbols = list(dict.fromkeys(
                contract["symbol"] for contract in samples
            ))[:8]

            response = data.option_market_data.get_option_snapshot(
                symbols=",".join(symbols),
                category="US_OPTION"
            )

            print("Snapshots HTTP:", response.status_code)

            if response.status_code != 200:
                print("Quote access unavailable")
                continue

            quotes = records(response.json())

            if not isinstance(quotes, list):
                print("Unknown quote response")
                continue

            lookup = {
                q["symbol"]: q
                for q in quotes
                if isinstance(q, dict)
                and isinstance(q.get("symbol"), str)
            }

            for contract in samples:
                symbol = contract["symbol"]
                quote = lookup.get(symbol)

                if quote is None:
                    print(symbol, "— Missing quote: SKIP")
                    continue

                result = assess_quote(quote)

                print(f"\n{symbol}")
                print("Bid:", quote.get("bid"))
                print("Ask:", quote.get("ask"))

                spread = result["spread_pct"]
                age = result["age_minutes"]

                print(
                    "Spread:",
                    f"{spread:.2f}%" if spread is not None
                    else "N/A"
                )
                print(
                    "Quote age:",
                    f"{age:.0f} minutes" if age is not None
                    else "N/A"
                )

                print(
                    "Research screening:",
                    "PASS" if result["research_eligible"]
                    else "BLOCKED"
                )

                for reason in result["blockers"]:
                    print("  BLOCK:", reason)

                for warning in result["warnings"]:
                    print("  NOTE:", warning)

                print("Trading authorization: DISABLED")

        except Exception as exc:
            print("Request stopped:", type(exc).__name__)
            print("Sensitive error details hidden")

    print("\nRESEARCH SCAN COMPLETE")
    print("NO ORDERS SENT")


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        print("SCANNER STOPPED:", type(exc).__name__)
        print("Review sandbox configuration locally")
