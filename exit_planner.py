"""JahvaScript hybrid exit planner.
Local simulations only. No trading API or AI calls.
"""

from decimal import Decimal, ROUND_CEILING

D = Decimal
MULTIPLIER = D("100")
MAX_FINAL_RUNNERS = 2


def plan(contracts, entry, exit_bid,
         entry_fees="0", exit_fee_each="0"):

    if type(contracts) is not int or contracts < 1:
        raise ValueError("Invalid contract quantity")

    entry = D(str(entry))
    bid = D(str(exit_bid))
    fees = D(str(entry_fees))
    exit_fee = D(str(exit_fee_each))

    values = (entry, bid, fees, exit_fee)

    if not all(x.is_finite() for x in values):
        raise ValueError("Non-finite price or fees")

    if entry <= 0 or bid <= 0 or fees < 0 or exit_fee < 0:
        raise ValueError("Invalid prices or fees")

    total_cost = entry * MULTIPLIER * contracts + fees
    net_per_sale = bid * MULTIPLIER - exit_fee

    if net_per_sale <= 0:
        raise ValueError("Invalid net sale proceeds")

    print("\n--- EXIT PLAN ---")
    print("Contracts:", contracts)
    print("Entry premium:", entry)
    print("Illustrative exit bid:", bid)
    print("Original total cost: $", total_cost)

    # Only calculate profit-taking if the planned
    # exit could cover the initial position cost.
    if net_per_sale * contracts < total_cost:
        print("Plan: NO PROFIT-TAKING SIGNAL")
        print("Separate loss/invalidation rules still apply")
        return

    needed = int(
        (total_cost / net_per_sale).to_integral_value(
            rounding=ROUND_CEILING
        )
    )

    half = (contracts + 1) // 2
    first_sale = min(contracts, max(half, needed))
    remaining = contracts - first_sale

    recovered = first_sale * net_per_sale
    floor_pnl = recovered - total_cost

    if remaining == 0:
        print("Plan: FULL EXIT")
    else:
        print("Plan: HYBRID SCALE-OUT")

    print("First sale quantity:", first_sale)
    print("Contracts remaining:", remaining)
    print("Net first-sale proceeds: $", recovered)
    print(
        "Original capital recovered:",
        "YES" if recovered >= total_cost else "NO"
    )
    print(
        "Gross position P&L if runners expire worthless,"
        " after modeled fees: $", floor_pnl
    )

    second_trim = max(
        0, remaining - MAX_FINAL_RUNNERS
    )

    if second_trim:
        print(
            "Additional contracts to trim at"
            " NEXT technical target:", second_trim
        )

    final_runners = remaining - second_trim
    print("Potential final runners:", final_runners)
    print("Protective orders: NOT PLACED")
    print("Execution: DISABLED")


print("\n=== JAHVASCRIPT EXIT PLANNER ===")
print("Mode: HYBRID | SIMULATION ONLY")

examples = [
    (10, "0.50", "1.00"),
    (10, "0.50", "0.75"),
    (3, "0.50", "1.00"),
    (1, "0.50", "1.00"),
]

for example in examples:
    plan(*example)

print("\nEXIT PLANNER TEST COMPLETE")
print("NO ORDERS SENT")
