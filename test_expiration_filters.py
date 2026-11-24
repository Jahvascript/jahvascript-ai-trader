import logging
from collections import Counter

logging.disable(logging.CRITICAL)

from webull.core.client import ApiClient
from webull.trade.trade_client import TradeClient
from webull.data.data_client import DataClient
from options_scanner import load_config, next_expiration

config = load_config()
expiry = next_expiration()

api = ApiClient(
    config["WEBULL_APP_KEY"],
    config["WEBULL_APP_SECRET"],
    "us"
)
api.add_endpoint("us", "api.sandbox.webull.com")

TradeClient(api)
data = DataClient(api)

tests = [
    ("START DATE ONLY", {"start_date": expiry}),
    ("END DATE ONLY", {"end_date": expiry}),
    ("BOTH DATES", {
        "start_date": expiry,
        "end_date": expiry
    })
]

print("\n=== EXPIRATION FILTER TEST ===")
print("Environment: SANDBOX")
print("Target expiration:", expiry)

for label, filters in tests:
    print(f"\n--- {label} ---")

    try:
        response = data.instrument.list_option_contracts(
            category="US_OPTION",
            underlying_symbols="SPY",
            status="LISTING",
            option_type="CALL",
            **filters
        )

        print("HTTP:", response.status_code)

        if response.status_code != 200:
            continue

        payload = response.json()

        if not isinstance(payload, dict):
            print("Unexpected response structure")
            continue

        contracts = payload.get("data", [])

        if not isinstance(contracts, list):
            print("Unexpected contract structure")
            continue

        dates = Counter(
            item.get("expiration_date")
            for item in contracts
            if isinstance(item, dict)
            and isinstance(item.get("expiration_date"), str)
        )

        print("Contracts:", len(contracts))
        print("Target expiration matches:", dates[expiry])
        print("Additional pages:", bool(
            payload.get("pagination_key")
        ))
        print("Earliest expirations:", sorted(dates)[:5])

    except Exception as exc:
        print("Request failed:", type(exc).__name__)
        print("Sensitive details hidden")

print("\nFILTER TEST COMPLETE")
print("NO ORDERS SENT")
