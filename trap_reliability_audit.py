"""JahvaScript trap-pattern reliability audit.
Historical, offline, descriptive research only.
"""
import json
import os
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent
JOURNAL = ROOT / "journal"
SOURCE = JOURNAL / "historical_trap_context.json"
STATS = JOURNAL / "historical_trap_statistics.json"
OUTPUT = JOURNAL / "trap_reliability_audit.json"

HORIZON = 3
CONFIRMED = "CONFIRMED_TRAP"
SURVIVED = "NO_FAILURE_WITHIN_HORIZON"
PENDING = "AWAITING_MORE_CANDLES"

def require(condition, message):
    if not condition:
        raise ValueError("STOP: " + message)

def rate(events, direction=None):
    chosen = [
        e for e in events
        if e["outcome"] != PENDING
        and (direction is None or e["direction"] == direction)
    ]
    failed = sum(e["outcome"] == CONFIRMED for e in chosen)
    total = len(chosen)
    return {
        "failed": failed,
        "resolved": total,
        "percent": round(100 * failed / total, 1)
                   if total else None
    }

def format_rate(result):
    p = result["percent"]
    return (
        f"{result['failed']}/{result['resolved']} "
        f"({p:.1f}%)" if p is not None else "N/A"
    )

def nonoverlap(events):
    selected = []
    last_index = {}

    for e in sorted(
        events,
        key=lambda x: (x["symbol"], x["day"], x["index"])
    ):
        key = (e["symbol"], e["day"])
        if e["index"] <= last_index.get(key, -100) + HORIZON:
            continue
        selected.append(e)
        last_index[key] = e["index"]

    return selected

def main():
    require(SOURCE.is_file(), "Context report missing")
    require(STATS.is_file(), "Statistics report missing")
    require(
        "journal/" in (ROOT / ".gitignore").read_text().splitlines(),
        "Journal must be gitignored"
    )

    context = json.loads(SOURCE.read_text())
    stats = json.loads(STATS.read_text())

    require(
        context.get("historical_research_only") is True
        and context.get("execution_enabled") is False
        and context.get("trade_authorization") is False,
        "Unsafe context"
    )
    require(
        stats.get("execution_enabled") is False
        and stats.get("trade_authorization") is False,
        "Unsafe statistics"
    )

    events = []
    per_symbol = {}
    timestamps = {}

    for symbol in ("SPY", "QQQ"):
        raw = json.loads(
            (JOURNAL / "history" / f"{symbol}_M5.json").read_text()
        )
        require(
            raw.get("execution_enabled") is False,
            "Unsafe candle source"
        )

        index = {
            bar["time"]: i
            for i, bar in enumerate(raw["bars"])
        }
        require(len(index) == len(raw["bars"]), "Duplicate bars")

        current = []

        for e in context["symbols"][symbol]["events"]:
            require(e["trade_signal"] is False, "Unsafe event")
            require(e["breakout_time_utc"] in index,
                    "Missing breakout candle")
            require(
                e["outcome"] in (CONFIRMED, SURVIVED, PENDING),
                "Unknown outcome"
            )

            t = datetime.fromisoformat(e["breakout_time_utc"])
            require(t.tzinfo is not None, "Unzoned event")

            current.append({
                "symbol": symbol,
                "day": e["session_date"],
                "index": index[e["breakout_time_utc"]],
                "time": t.isoformat(),
                "direction": e["direction"],
                "vwap": e["vwap_position"],
                "outcome": e["outcome"]
            })

        per_symbol[symbol] = current
        timestamps[symbol] = {
            e["time"]: e["direction"] for e in current
        }
        events.extend(current)

    totals = Counter(e["outcome"] for e in events)
    require(
        len(events) == stats["breakout_events"]
        and totals[CONFIRMED] == stats["confirmed_reversals"]
        and totals[SURVIVED] ==
            stats["no_failure_within_horizon"]
        and totals[PENDING] == stats["pending_excluded"],
        "Statistics reconciliation failed"
    )

    print("\n=== JAHVASCRIPT PATTERN RELIABILITY AUDIT ===")
    print("Total events:", len(events))
    print("Resolved events:", rate(events)["resolved"])
    print("Pending excluded:", totals[PENDING])

    print("\nDIRECTION BY SYMBOL")
    for symbol, rows in per_symbol.items():
        print(symbol)
        for direction in ("UP", "DOWN"):
            print(
                " ", direction,
                format_rate(rate(rows, direction))
            )

    overlapping = set()
    pairs = 0

    for symbol, rows in per_symbol.items():
        days = sorted(set(e["day"] for e in rows))
        for day in days:
            group = sorted(
                (e for e in rows if e["day"] == day),
                key=lambda e: e["index"]
            )

            for a, b in zip(group, group[1:]):
                if b["index"] - a["index"] <= HORIZON:
                    pairs += 1
                    overlapping.add((symbol, a["index"]))
                    overlapping.add((symbol, b["index"]))

    print("\nEVENT DEPENDENCE")
    print("Adjacent overlapping-window pairs:", pairs)
    print("Events involved in overlaps:", len(overlapping))

    shared = set(timestamps["SPY"]) & set(timestamps["QQQ"])
    aligned = sum(
        timestamps["SPY"][t] == timestamps["QQQ"][t]
        for t in shared
    )
    print("SPY/QQQ simultaneous breakouts:", len(shared))
    print("Same-direction simultaneous breakouts:", aligned)

    reduced = nonoverlap(events)
    print("\nNON-OVERLAPPING SUBSAMPLE")
    print("Selected events:", len(reduced))
    print("Pending:", sum(e["outcome"] == PENDING for e in reduced))
    for direction in ("UP", "DOWN"):
        print(direction, format_rate(rate(reduced, direction)))

    print("\nDIRECTION / VWAP CROSS-CHECK")
    for direction in ("UP", "DOWN"):
        for position in ("ABOVE", "BELOW"):
            group = [
                e for e in events
                if e["direction"] == direction
                and e["vwap"] == position
            ]
            print(
                direction, position,
                format_rate(rate(group))
            )

    print("\nLEAVE-ONE-DAY-OUT CHECK")
    all_days = sorted(set(e["day"] for e in events))
    day_checks = []

    for excluded in all_days:
        subset = [e for e in events if e["day"] != excluded]
        down = rate(subset, "DOWN")
        up = rate(subset, "UP")

        if down["percent"] is None or up["percent"] is None:
            continue

        difference = round(
            down["percent"] - up["percent"], 1
        )
        day_checks.append({
            "excluded_day": excluded,
            "down_minus_up_percentage_points": difference
        })
        print(
            excluded,
            "| DOWN minus UP:",
            f"{difference:+.1f} percentage points"
        )

    report = {
        "created_utc": datetime.now(timezone.utc).isoformat(),
        "total_events": len(events),
        "resolved": rate(events)["resolved"],
        "overlapping_adjacent_pairs": pairs,
        "events_in_overlapping_windows": len(overlapping),
        "simultaneous_spy_qqq_breakouts": len(shared),
        "same_direction_simultaneous": aligned,
        "nonoverlapping_sample_size": len(reduced),
        "nonoverlapping_direction_rates": {
            d: rate(reduced, d) for d in ("UP", "DOWN")
        },
        "leave_one_day_out": day_checks,
        "source_prices_verified": False,
        "predictive_validity_established": False,
        "historical_research_only": True,
        "execution_enabled": False,
        "trade_authorization": False
    }

    os.umask(0o077)
    OUTPUT.write_text(json.dumps(report, indent=2) + "\n")
    OUTPUT.chmod(0o600)

    print("\nSaved: trap_reliability_audit.json")
    print("Independent observations: NOT ASSUMED")
    print("Predictive validity: NOT ESTABLISHED")
    print("\nPATTERN RELIABILITY AUDIT PASSED")
    print("AI REQUESTS: NONE")
    print("WEBULL REQUESTS: NONE")
    print("ORDER EXECUTION: DISABLED")

if __name__ == "__main__":
    main()
