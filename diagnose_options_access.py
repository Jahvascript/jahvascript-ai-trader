import logging
import re
from pathlib import Path

logging.disable(logging.CRITICAL)

from webull.core.client import ApiClient
from webull.trade.trade_client import TradeClient
from webull.data.data_client import DataClient

env = {}

for line in Path(".env").read_text().splitlines():
    if "=" in line and not line.lstrip().startswith("#"):
        key, value = line.split("=", 1)
        env[key.strip()] = value.strip()

if env.get("WEBULL_ENVIRONMENT") != "uat":
    raise SystemExit("BLOCKED: Sandbox required")

print("\n=== OPTIONS ACCESS DIAGNOSTIC ===")
print("Environment: SANDBOX")
print("Mode: READ ONLY")

try:
    api = ApiClient(
        env["WEBULL_APP_KEY"],
        env["WEBULL_APP_SECRET"],
        "us"
    )
    api.add_endpoint("us", "api.sandbox.webull.com")

    trade = TradeClient(api)

    account_response = trade.account_v2.get_account_list()
    print("Trading authentication:",
          account_response.status_code)

    if account_response.status_code != 200:
        raise RuntimeError("Account authentication failed")

    # Reuse the initialized API client
    data = DataClient(api)

    response = data.instrument.list_option_contracts(
        category="US_OPTION",
        underlying_symbols="SPY",
        status="LISTING"
    )

    print("Options endpoint HTTP:", response.status_code)

    if response.status_code == 200:
        payload = response.json()

        if isinstance(payload, dict):
            contracts = payload.get("data", [])
        elif isinstance(payload, list):
            contracts = payload
        else:
            contracts = []

        if isinstance(contracts, list):
            print("Contracts returned:", len(contracts))
            for contract in contracts[:3]:
                if isinstance(contract, dict):
                    print(
                        "Contract:",
                        contract.get("symbol", "Unknown")
                    )
        else:
            print("Response format needs mapping")

except Exception as exc:
    details = str(exc)

    status = re.search(
        r"HTTP Status:\s*(\d{3})", details
    )
    code = re.search(
        r"\bCode:\s*([A-Za-z0-9_]+)", details
    )

    print("Error type:", type(exc).__name__)
    print(
        "HTTP status:",
        status.group(1) if status else "Unavailable"
    )
    print(
        "Error code:",
        code.group(1) if code else "Unavailable"
    )

print("\nDIAGNOSTIC COMPLETE — NO ORDERS SENT")
