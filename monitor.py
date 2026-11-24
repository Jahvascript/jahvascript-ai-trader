from pathlib import Path
from datetime import datetime
import os

from webull.core.client import ApiClient
from webull.trade.trade_client import TradeClient

# Protect any locally cached authentication tokens
os.umask(0o077)

ROOT = Path(__file__).resolve().parent

def load_config():
    config = {}
    for line in (ROOT / ".env").read_text().splitlines():
        if "=" in line and not line.startswith("#"):
            key, value = line.split("=", 1)
            config[key.strip()] = value.strip()

    if config.get("WEBULL_ENVIRONMENT") != "uat":
        raise RuntimeError("Sandbox environment required")
    return config

def require_success(response, operation):
    if response.status_code != 200:
        raise RuntimeError(
            f"{operation} returned HTTP {response.status_code}"
        )
    return response.json()

def extract_list(payload, keys):
    if isinstance(payload, list):
        return payload
    if isinstance(payload, dict):
        for key in keys:
            value = payload.get(key)
            if isinstance(value, list):
                return value
            if isinstance(value, dict):
                found = extract_list(value, keys)
                if found is not None:
                    return found
    return None

def show_balance(data):
    allowed = {
        "cash_balance", "settled_cash", "unsettled_cash",
        "buying_power", "option_buying_power",
        "options_buying_power", "net_liquidation",
        "net_liquidation_value", "available_funds",
        "total_cash", "total_cash_balance",
        "equity", "market_value"
    }

    found = []

    def scan(obj):
        if isinstance(obj, dict):
            for key, value in obj.items():
                if isinstance(value, (dict, list)):
                    scan(value)
                elif key.lower() in allowed:
                    found.append((key, value))
        elif isinstance(obj, list):
            for item in obj:
                scan(item)

    scan(data)

    if not found:
        print("Balance retrieved; no known display fields.")
    else:
        for key, value in found[:20]:
            print(f"  {key}: {value}")

def main():
    config = load_config()

    client = ApiClient(
        config["WEBULL_APP_KEY"],
        config["WEBULL_APP_SECRET"],
        "us"
    )
    client.add_endpoint("us", "api.sandbox.webull.com")
    trade = TradeClient(client)

    print("\n=== JAHVASCRIPT ACCOUNT MONITOR ===")
    print("Environment: SANDBOX")
    print("Mode: READ ONLY")
    print("Time:", datetime.now().astimezone().isoformat(timespec="seconds"))

    accounts_data = require_success(
        trade.account_v2.get_account_list(),
        "Account list"
    )
    accounts = extract_list(
        accounts_data, ("accounts", "data", "items")
    )

    if accounts is None:
        raise RuntimeError("Unrecognized account response format")

    print("Accounts:", len(accounts))

    for index, account in enumerate(accounts, 1):
        account_id = str(account["account_id"])

        print(f"\n--- Account {index} ---")
        print("Type:", account.get("account_type", "Unknown"))
        print("Account ID ending:", account_id[-4:])

        balances = require_success(
            trade.account_v2.get_account_balance(account_id),
            "Balances"
        )
        print("\nBALANCES")
        show_balance(balances)

        positions_data = require_success(
            trade.account_v2.get_account_position(account_id),
            "Positions"
        )
        positions = extract_list(
            positions_data, ("positions", "data", "items")
        )

        print("\nPOSITIONS")
        if positions is None:
            print("Retrieved; response format needs mapping.")
        else:
            print("Count:", len(positions))
            for position in positions:
                print(
                    "  ",
                    position.get("symbol", "Unknown symbol"),
                    "Quantity:",
                    position.get("quantity", "Unknown")
                )

    print("\nMONITOR CHECK COMPLETE — NO ORDERS PLACED")

if __name__ == "__main__":
    try:
        main()
    except Exception as error:
        print("Monitor failed:", type(error).__name__)
        print("Check the sandbox configuration and API status.")
