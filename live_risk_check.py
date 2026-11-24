import json
import os
from decimal import Decimal, InvalidOperation
from pathlib import Path

from webull.core.client import ApiClient
from webull.trade.trade_client import TradeClient
from risk_engine import evaluate

os.umask(0o077)
ROOT = Path(__file__).resolve().parent


def number(data, *keys):
    if not isinstance(data, dict):
        return None
    for key in keys:
        value = data.get(key)
        if value is None or isinstance(value, bool):
            continue
        try:
            result = Decimal(str(value))
            if result.is_finite():
                return result
        except (ValueError, InvalidOperation):
            pass
    return None


def get_json(response, title):
    if response.status_code != 200:
        raise RuntimeError(
            f"{title}: HTTP {response.status_code}"
        )
    return response.json()


def as_list(payload, names):
    if isinstance(payload, list):
        return payload
    if isinstance(payload, dict):
        for key in names:
            if key in payload:
                return as_list(payload[key], names)
    return None


def normalize(payload, account_type):
    if isinstance(payload, dict) and isinstance(
        payload.get("data"), dict
    ):
        payload = payload["data"]

    if not isinstance(payload, dict):
        return None, "Balance structure unknown"

    if payload.get("total_asset_currency") not in (
        None, "USD"
    ):
        return None, "Account totals are not USD"

    assets = payload.get("account_currency_assets")
    if not isinstance(assets, list):
        return None, "USD balance breakdown missing"

    usd = [
        x for x in assets
        if isinstance(x, dict)
        and x.get("currency") == "USD"
    ]

    if len(usd) != 1:
        return None, "Unambiguous USD balance missing"

    usd = usd[0]

    equity = number(
        payload, "total_net_liquidation_value"
    )
    if equity is None:
        equity = number(usd, "net_liquidation_value")

    day_pnl = number(payload, "total_day_profit_loss")
    if day_pnl is None:
        day_pnl = number(usd, "day_profit_loss")

    bp = number(
        usd, "options_buying_power", "option_buying_power"
    )
    bp_note = "Broker-reported options buying power"

    if bp is None and "CASH" in account_type:
        settled = number(usd, "settled_cash")
        general_bp = number(usd, "buying_power")

        if settled is not None and general_bp is not None:
            bp = min(settled, general_bp)
            bp_note = (
                "Cash proxy only; options buying "
                "power NOT verified"
            )

    missing = []

    if equity is None or equity <= 0:
        missing.append("net liquidation value")
    if day_pnl is None:
        missing.append("day P&L")
    if bp is None or bp < 0:
        missing.append("buying-power estimate")

    if missing:
        return None, "Missing/invalid: " + ", ".join(missing)

    return {
        "environment": "uat",
        "equity": str(equity),
        "options_buying_power": str(bp),
        "day_pnl": str(day_pnl),
        "open_option_max_loss": "0",
        "open_positions": 0
    }, bp_note


def main():
    env = {}

    for line in (ROOT / ".env").read_text().splitlines():
        if "=" in line and not line.strip().startswith("#"):
            key, val = line.split("=", 1)
            env[key.strip()] = val.strip()

    if env.get("WEBULL_ENVIRONMENT") != "uat":
        raise RuntimeError(
            "Sandbox-only protection triggered"
        )

    config = json.loads(
        (ROOT / "risk_config.json").read_text()
    )

    if config.get("paper_only") is not True:
        raise RuntimeError(
            "Paper-only protection triggered"
        )

    api = ApiClient(
        env["WEBULL_APP_KEY"],
        env["WEBULL_APP_SECRET"],
        "us"
    )
    api.add_endpoint("us", "api.sandbox.webull.com")
    trade = TradeClient(api)

    accounts = as_list(
        get_json(
            trade.account_v2.get_account_list(),
            "Accounts"
        ),
        ("accounts", "data", "items")
    )

    if accounts is None:
        raise RuntimeError("Unrecognized account list")

    proposed_trade = {
        "strategy": "LONG_CALL",
        "contracts": 1,
        "premium": "0.50",
        "multiplier": 100
    }

    print("\n=== JAHVASCRIPT WEBULL RISK CHECK ===")
    print("SANDBOX ONLY | SIMULATION ONLY | NO ORDERS")
    print("Accounts:", len(accounts))

    for i, acct in enumerate(accounts, 1):
        print(f"\nAccount {i}")

        if not isinstance(acct, dict) or not acct.get(
            "account_id"
        ):
            print("BLOCKED: Unrecognized account record")
            continue

        kind = str(
            acct.get("account_type", "UNKNOWN")
        ).upper()
        print("Type:", kind)

        if "CASH" not in kind and "MARGIN" not in kind:
            print("BLOCKED: Account type unsupported")
            continue

        aid = str(acct["account_id"])

        balances = get_json(
            trade.account_v2.get_account_balance(aid),
            "Balance"
        )

        positions_payload = get_json(
            trade.account_v2.get_account_position(aid),
            "Positions"
        )

        positions = as_list(
            positions_payload,
            ("positions", "data", "items")
        )

        if positions is None:
            print("BLOCKED: Position response not understood")
            continue

        print("Positions:", len(positions))

        if positions:
            print(
                "BLOCKED: Existing positions need "
                "an exposure model"
            )
            continue

        normalized, note = normalize(balances, kind)

        if normalized is None:
            print("BLOCKED:", note)
            continue

        print("Balance mapping:", note)

        allowed, reasons, risk = evaluate(
            normalized,
            proposed_trade,
            config
        )

        print(
            "Simulated $0.50 call budget check:",
            "PASS" if allowed else "BLOCKED"
        )

        if risk is not None:
            print(
                "Hypothetical modeled loss: "
                f"${risk:.2f}"
            )

        for reason in reasons:
            print("Reason:", reason)

    print("\nREAD-ONLY CHECK COMPLETE — NO ORDERS SENT")
    print("Live-trading authorization: DISABLED")


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        print("CHECK STOPPED:", type(exc).__name__)
        if isinstance(exc, RuntimeError):
            message = str(exc)
            safe_prefixes = (
                "Accounts: HTTP",
                "Balance: HTTP",
                "Positions: HTTP",
                "Unrecognized",
                "Sandbox-only",
                "Paper-only"
            )
            if message.startswith(safe_prefixes):
                print(message)
