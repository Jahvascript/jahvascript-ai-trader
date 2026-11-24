import json
from decimal import Decimal, InvalidOperation
from pathlib import Path

D = Decimal

def money(value):
    try:
        n = D(str(value))
    except (InvalidOperation, ValueError):
        raise ValueError("Invalid number")
    if not n.is_finite():
        raise ValueError("Non-finite number")
    return n

def evaluate(account, trade, config):
    """Local simulation only. Never submits orders."""
    reasons = []

    try:
        if config.get("paper_only") is not True:
            return False, ["Paper-only mode required"], None

        if account.get("environment") != "uat":
            return False, ["Sandbox account required"], None

        if trade.get("strategy") not in ("LONG_CALL", "LONG_PUT"):
            return False, ["Strategy is not enabled"], None

        qty = trade.get("contracts")
        if (
            type(qty) is not int
            or not 1 <= qty <= int(config["max_contracts_per_trade"])
        ):
            return False, ["Invalid contract count"], None

        equity = money(account["equity"])
        bp = money(account["options_buying_power"])
        pnl = money(account["day_pnl"])
        exposure = money(account["open_option_max_loss"])
        premium = money(trade["premium"])
        count = account["open_positions"]

        if (
            equity <= 0 or bp < 0 or exposure < 0
            or premium <= 0 or type(count) is not int
            or count < 0
        ):
            return False, ["Invalid account or quote data"], None

        if count >= int(config["max_open_positions"]):
            reasons.append("Maximum open positions reached")

        if trade.get("multiplier") != 100:
            reasons.append("Only standard 100-share options enabled")

        cost = premium * D(100) * qty
        risk = cost + money(config["fee_buffer_per_contract"]) * qty

        if risk > equity * money(config["max_risk_per_trade_pct"]) / 100:
            reasons.append("Per-trade risk limit exceeded")

        if exposure + risk > equity * money(
            config["max_total_option_risk_pct"]
        ) / 100:
            reasons.append("Total options exposure limit exceeded")

        if max(D(0), -pnl) + risk > equity * money(
            config["max_daily_loss_pct"]
        ) / 100:
            reasons.append("Daily loss budget exceeded")

        if risk > bp:
            reasons.append("Insufficient options buying power")

        return not reasons, reasons, risk

    except (KeyError, ValueError, TypeError,
            InvalidOperation, ArithmeticError):
        return False, ["Invalid or missing inputs: BLOCKED"], None

if __name__ == "__main__":
    config = json.loads(Path("risk_config.json").read_text())

    # Hypothetical test account, NOT your actual Webull balance
    account = {
        "environment": "uat",
        "equity": "10000",
        "options_buying_power": "1000",
        "day_pnl": "0",
        "open_option_max_loss": "0",
        "open_positions": 0
    }

    tests = [
        {"strategy": "LONG_CALL", "contracts": 1,
         "premium": "0.50", "multiplier": 100},
        {"strategy": "LONG_PUT", "contracts": 1,
         "premium": "2.50", "multiplier": 100},
        {"strategy": "IRON_CONDOR", "contracts": 1,
         "premium": "0.50", "multiplier": 100}
    ]

    print("\n=== JAHVASCRIPT RISK ENGINE ===")
    print("MODE: SIMULATION ONLY\n")

    for trade in tests:
        approved, reasons, risk = evaluate(account, trade, config)

        print(
            f"{trade['strategy']}: "
            f"{'PASS' if approved else 'BLOCKED'}"
        )

        if risk is not None:
            print(f"  Modeled maximum loss: ${risk:.2f}")

        for reason in reasons:
            print(" ", reason)

    print("\nNO ORDERS SENT")
