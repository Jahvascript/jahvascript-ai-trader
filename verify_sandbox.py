from pathlib import Path
from webull.core.client import ApiClient
from webull.trade.trade_client import TradeClient

# Read locally stored credentials
env = {}
for line in Path(".env").read_text().splitlines():
    if "=" in line and not line.startswith("#"):
        key, value = line.split("=", 1)
        env[key.strip()] = value.strip()

app_key = env["WEBULL_APP_KEY"]
app_secret = env["WEBULL_APP_SECRET"]

if env.get("WEBULL_ENVIRONMENT") != "uat":
    raise SystemExit("Safety check failed: not configured for sandbox.")

# Force sandbox endpoint — never production
client = ApiClient(app_key, app_secret, "us")
client.add_endpoint("us", "api.sandbox.webull.com")

trade = TradeClient(client)

print("Connecting to Webull SANDBOX...")

try:
    response = trade.account_v2.get_account_list()
    print("HTTP Status:", response.status_code)

    if response.status_code == 200:
        data = response.json()
        accounts = data if isinstance(data, list) else data.get("data", data.get("accounts", []))

        print("SUCCESS: Sandbox connection established!")

        if isinstance(accounts, list):
            print("Accounts found:", len(accounts))
            for account in accounts:
                if isinstance(account, dict):
                    print("Account type:", account.get("account_type", "Unknown"))
                    print("Account ID ending:", str(account.get("account_id", ""))[-4:])
        else:
            print("Response structure:", type(accounts).__name__)
    else:
        print("Connection rejected. Share only the HTTP status code.")

except Exception as e:
    print("Connection failed.")
    print("Error type:", type(e).__name__)
