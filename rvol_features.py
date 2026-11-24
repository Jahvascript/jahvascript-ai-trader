"""JahvaScript dual-RVOL feature calculator.

Pure arithmetic. No market-data requests, AI, or orders.
Caller must supply only prior observations.
"""

from decimal import Decimal

THRESHOLD = Decimal("1.2")
REFERENCE_COUNT = 5


def _volume(value):
    number = Decimal(str(value))

    if not number.is_finite() or number < 0:
        raise ValueError("Volume must be finite and nonnegative")

    return number


def _baseline(values, label):
    if len(values) != REFERENCE_COUNT:
        raise ValueError(label + ": exactly five values required")

    numbers = [_volume(value) for value in values]
    average = sum(numbers, Decimal(0)) / REFERENCE_COUNT

    if average <= 0:
        raise ValueError(label + ": baseline must be positive")

    return average


def calculate_dual_rvol(
    current_volume,
    preceding_five_volumes,
    prior_five_same_slot_volumes
):
    current = _volume(current_volume)

    rolling_base = _baseline(
        preceding_five_volumes,
        "Rolling baseline"
    )

    clock_base = _baseline(
        prior_five_same_slot_volumes,
        "Clock-time baseline"
    )

    rolling = current / rolling_base
    clock = current / clock_base

    rolling_group = (
        "HIGH" if rolling >= THRESHOLD else "LOW_NORMAL"
    )

    clock_group = (
        "HIGH" if clock >= THRESHOLD else "LOW_NORMAL"
    )

    return {
        "rolling_rvol": rolling,
        "clock_rvol": clock,
        "rolling_group": rolling_group,
        "clock_group": clock_group,
        "agreement_state": rolling_group + "_TO_" + clock_group,
        "trade_signal": False
    }
