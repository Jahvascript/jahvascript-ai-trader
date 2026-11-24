"""JahvaScript trap detector prototype.
Completed candles only. No brokerage or AI requests.
"""
import math


def classify(candles, support, resistance, horizon=3):
    if (
        not math.isfinite(support)
        or not math.isfinite(resistance)
        or support >= resistance
        or horizon < 1
    ):
        raise ValueError("Invalid levels or horizon")

    if len(candles) < 2:
        raise ValueError("Need two completed candles")

    closes = []
    volumes = []

    for bar in candles:
        close = float(bar["close"])
        volume = float(bar["volume"])

        if (
            not math.isfinite(close)
            or close <= 0
            or not math.isfinite(volume)
            or volume < 0
        ):
            raise ValueError("Invalid candle")

        closes.append(close)
        volumes.append(volume)

    confirmed = []
    pending = []

    for i in range(1, len(closes)):
        bull_break = (
            closes[i - 1] <= resistance < closes[i]
        )
        bear_break = (
            closes[i - 1] >= support > closes[i]
        )

        if not (bull_break or bear_break):
            continue

        kind = "BULL_TRAP" if bull_break else "BEAR_TRAP"
        level = resistance if bull_break else support

        future = closes[i + 1:i + 1 + horizon]

        failure = next(
            (
                step
                for step, price in enumerate(future, 1)
                if (
                    price < level
                    if bull_break
                    else price > level
                )
            ),
            None
        )

        recent_volume = volumes[max(0, i - 5):i]
        average = sum(recent_volume) / len(recent_volume)

        rvol = (
            round(volumes[i] / average, 2)
            if average > 0
            else None
        )

        event = {
            "kind": kind,
            "breakout_bar": i,
            "confirmation_bar": (
                i + failure if failure else None
            ),
            "breakout_relative_volume": rvol
        }

        if failure is not None:
            confirmed.append(event)
        elif len(future) < horizon:
            pending.append(event)

    recent = [
        e for e in confirmed
        if e["breakout_bar"] >= len(closes) - 12
    ]

    directions = {e["kind"] for e in recent}

    if len(directions) == 2:
        regime = "DOUBLE_FAKEOUT"
        risk = "HOLD_NEW_ENTRIES"
    elif directions:
        regime = next(iter(directions))
        risk = "CAUTION_REVIEW"
    else:
        regime = "NO_CONFIRMED_RECENT_TRAP"
        risk = "NORMAL_RESEARCH"

    return {
        "regime": regime,
        "risk_status": risk,
        "confirmed": confirmed,
        "pending": pending,
        "execution_enabled": False
    }


def sample(closes):
    return [
        {
            "close": price,
            "volume": 1000 + (i % 3) * 300
        }
        for i, price in enumerate(closes)
    ]


print("\n=== JAHVASCRIPT TRAP DETECTOR ===")
print("STEP 15C: SYNTHETIC DATA TEST\n")

scenarios = [
    (
        "Bull trap",
        [100, 106, 103, 102, 101, 100],
        "BULL_TRAP"
    ),
    (
        "Bear trap",
        [100, 94, 98, 100, 101, 102],
        "BEAR_TRAP"
    ),
    (
        "Double fakeout",
        [100, 107, 102, 93, 98, 101],
        "DOUBLE_FAKEOUT"
    ),
    (
        "Clean upward break",
        [100, 106, 108, 109, 110, 111],
        "NO_CONFIRMED_RECENT_TRAP"
    )
]

for name, prices, expected in scenarios:
    result = classify(
        sample(prices),
        support=95,
        resistance=105
    )

    assert result["regime"] == expected
    assert result["execution_enabled"] is False

    print(name)
    print("  Regime:", result["regime"])
    print("  Risk:", result["risk_status"])
    print("  Confirmed:", len(result["confirmed"]))
    print("  Pending:", len(result["pending"]))
    print()

print("TRAP DETECTOR TEST COMPLETE")
print("MARKET DATA: SYNTHETIC ONLY")
print("AI REQUESTS: NONE")
print("ORDER EXECUTION: DISABLED")
