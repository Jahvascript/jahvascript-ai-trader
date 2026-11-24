import logging
import re
from pathlib import Path

logging.disable(logging.CRITICAL)

from webull.core.client import ApiClient
from webull.trade.trade_client import TradeClient

env = {}

for line in Path(".env").read_text().splitlines():
    if "=" in line and not line.lstrip().startswith("#"):
        key, value = line.split("=", 1)
        env[key.strip()] = value.strip()

if env.get("WEBULL_ENVIRONMENT") != "uat":
    raise SystemExit("BLOCKED: Sandbox required")

api = ApiClient(
    env["WEBULL_APP_KEY"],
    env["WEBULL_APP_SECRET"],
    "us"
)
api.add_endpoint("us", "api.sandbox.webull.com")

def check(name, function):
    try:
        response = function()
        print(name, "HTTP:", response.status_code)
        return response.json() if response.status_code == 200 else None
    except Exception as exc:
        details = str(exc)
        status = re.search(r"HTTP Status:\s*(\d{3})", details)
        code = re.search(r"\bCode:\s*([A-Za-z0-9_]+)", details)
        print(name, "HTTP:", status.group(1) if status else "Unknown")
        print("Error:", code.group(1) if code else type(exc).__name__)
        return None

print("\n=== JAHVASCRIPT INSTRUMENT DIAGNOSTIC ===")
print("SANDBOX | READ ONLY")

try:
    trade = TradeClient(api)

    accounts = check(
        "Account API",
        trade.account_v2.get_account_list
    )

    result = check(
        "Instrument API",
        lambda: trade.trade_instrument.get_tradeable_instruments(
            page_size=10
        )
    )

    if result is not None:
        payload = result.get("data", result) if isinstance(result, dict) else result

        if isinstance(payload, dict):
            print("Response fields:", list(payload.keys())[:15])

            items = (
                payload.get("instruments")
                or payload.get("items")
                or payload.get("records")
                or []
            )
        elif isinstance(payload, list):
            items = payload
        else:
            items = []

        print("Instruments found:", len(items))

        for item in items[:5]:
            if isinstance(item, dict):
                print(
                    "Symbol:", item.get("symbol", "N/A"),
                    "| Type:", item.get(
                        "instrument_type",
                        item.get("security_type", "Unknown")
                    )
                )

except Exception as exc:
    print("Diagnostic stopped:", type(exc).__name__)

print("\nDIAGNOSTIC COMPLETE")
print("NO ORDERS SENT")
