import logging
from collections import Counter

logging.disable(logging.CRITICAL)

from webull.core.client import ApiClient
from webull.trade.trade_client import TradeClient
from webull.data.data_client import DataClient

from options_scanner import (
    load_config,
    records,
    next_expiration
)

config = load_config()

api = ApiClient(
    config["WEBULL_APP_KEY"],
    config["WEBULL_APP_SECRET"],
    "us"
)
api.add_endpoint("us", "api.sandbox.webull.com")

TradeClient(api)
data = DataClient(api)

expiry = next_expiration()

print("\n=== OPTIONS FILTER DIAGNOSTIC ===")
print("Environment: SANDBOX")
print("Expiration requested:", expiry)

fields = [
    "underlying_symbol",
    "expiration_date",
    "option_type",
    "status",
    "tradable_status",
    "currency",
    "def_type",
    "settlement_method",
    "multiplier"
]

for ticker in ("SPY", "QQQ"):
    print(f"\n--- {ticker} ---")

    try:
        response = data.instrument.list_option_contracts(
            category="US_OPTION",
            underlying_symbols=ticker,
            status="LISTING",
            start_date=expiry
        )

        print("HTTP:", response.status_code)

        if response.status_code != 200:
            continue

        payload = response.json()
        contracts = records(payload)

        if not isinstance(contracts, list):
            print("Unknown response structure")
            continue

        print("Contracts:", len(contracts))

        if isinstance(payload, dict):
            print(
                "Additional pages:",
                bool(payload.get("pagination_key"))
            )

        if not contracts:
            continue

        example = next(
            (c for c in contracts if isinstance(c, dict)),
            None
        )

        if example:
            print("\nFIRST CONTRACT FIELDS:")
            for field in fields:
                print(
                    f"  {field}:",
                    example.get(field, "MISSING")
                )

        print("\nFIELD DISTRIBUTION:")

        for field in fields:
            counts = Counter(
                str(c.get(field, "MISSING"))
                for c in contracts
                if isinstance(c, dict)
            )
            print(
                f"  {field}:",
                counts.most_common(3)
            )

        matches = [
            c for c in contracts
            if isinstance(c, dict)
            and c.get("underlying_symbol") == ticker
            and c.get("expiration_date") == expiry
        ]

        print(
            "\nExact underlying + expiry matches:",
            len(matches)
        )

    except Exception as exc:
        print("Diagnostic error:", type(exc).__name__)

print("\nDIAGNOSTIC COMPLETE")
print("NO ORDERS SENT")
