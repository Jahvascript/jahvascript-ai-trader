from pathlib import Path
from webull.core.client import ApiClient
from webull.trade.trade_client import TradeClient

# Load local configuration
env = {}
for line in Path(".env").read_text().splitlines():
    if "=" in line and not line.startswith("#"):
        key, value = line.split("=", 1)
        env[key.strip()] = value.strip()

# Never connect to production in this phase
if env.get("WEBULL_ENVIRONMENT") != "uat":
    raise SystemExit("BLOCKED: Sandbox mode required.")

client = ApiClient(
    env["WEBULL_APP_KEY"],
    env["WEBULL_APP_SECRET"],
    "us"
)
client.add_endpoint("us", "api.sandbox.webull.com")
trade = TradeClient(client)

print("\n=== JAHVASCRIPT AI TRADER ===")
print("Environment: SANDBOX")
print("Mode: READ ONLY\n")

try:
    response = trade.account_v2.get_account_list()

    if response.status_code != 200:
        raise RuntimeError(
            f"Account list HTTP {response.status_code}"
        )

    result = response.json()
    accounts = result if isinstance(result, list) else (
        result.get("data") or result.get("accounts") or []
    )

    if isinstance(accounts, dict):
        accounts = accounts.get("accounts", [])

    print(f"Accounts discovered: {len(accounts)}")

    for index, account in enumerate(accounts, 1):
        account_id = str(account["account_id"])
        account_type = account.get("account_type", "Unknown")

        print(f"\n--- Account {index} ---")
        print("Type:", account_type)
        print("ID ending:", account_id[-4:])

        balance = trade.account_v2.get_account_balance(
            account_id
        )

        print("Balance HTTP:", balance.status_code)

        if balance.status_code == 200:
            print("Balance query: SUCCESS")

            data = balance.json()
            metrics = []

            def find_metrics(obj):
                if isinstance(obj, dict):
                    for key, value in obj.items():
                        name = key.lower()
                        allowed = (
                            "balance", "buying_power",
                            "cash", "liquidation",
                            "market_value", "currency"
                        )
                        if isinstance(value, (dict, list)):
                            find_metrics(value)
                        elif (
                            any(x in name for x in allowed)
                            and not any(x in name for x in
                                        ("id", "token", "secret", "key"))
                        ):
                            metrics.append((key, value))
                elif isinstance(obj, list):
                    for item in obj:
                        find_metrics(item)

            find_metrics(data)

            for key, value in metrics[:15]:
                print(f"{key}: {value}")

            if not metrics:
                print("No recognized balance fields.")
                print("Response type:", type(data).__name__)

except Exception as exc:
    print("ERROR TYPE:", type(exc).__name__)
    print("Check your local Terminal for details.")

print("\nRead-only check finished.")
