"""JahvaScript Intelligence Priority Engine.
Local, read-only research. No trading or AI APIs.
"""

import json
import os
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

from market_intelligence_v2 import classify

ROOT = Path(__file__).resolve().parent
INPUT = ROOT / "journal/discord_market_updates.jsonl"
OUTPUT = ROOT / "journal/intelligence_brief_latest.json"

SOURCE_ID = "800519694754644029"

# Watchlist for relevance research, not verified ETF holdings.
DIRECT = {"SPY", "QQQ"}
RELATED = {
    "NVDA", "MSFT", "AAPL", "AMZN",
    "META", "GOOGL", "GOOG", "TSLA", "AVGO"
}

def freshness(timestamp, now):
    try:
        t = datetime.fromisoformat(timestamp)
        if t.tzinfo is None:
            return "INVALID_TIME", None

        age = (now - t).total_seconds() / 60

        if age < -2:
            return "INVALID_TIME", None
        if age <= 15:
            return "RECENT_DISCORD_POST", round(max(age, 0), 1)
        if age <= 60:
            return "OLDER_DISCORD_POST", round(age, 1)

        return "ARCHIVE_ONLY", round(age, 1)
    except (ValueError, TypeError):
        return "INVALID_TIME", None

def prioritize(record, now):
    result = classify(record)
    tags = set(result["tags"])
    tickers = set(result["tickers"])

    if "MARKET_DISRUPTION" in tags or tickers & DIRECT:
        priority = "HIGH_REVIEW"
        rank = 3
    elif (
        tags & {"MACRO_ECONOMY", "GOVERNMENT_POLICY"}
        or tickers & RELATED
    ):
        priority = "REVIEW"
        rank = 2
    elif tags == {"UNCATEGORIZED"}:
        priority = "UNCATEGORIZED"
        rank = 0
    else:
        priority = "BACKGROUND"
        rank = 1

    status, age = freshness(
        record.get("discord_timestamp"), now
    )

    if status in {"INVALID_TIME", "ARCHIVE_ONLY"}:
        priority = "ARCHIVE_ONLY"
        rank = -1

    return {
        "message_id": result["id"],
        "discord_timestamp": record.get("discord_timestamp"),
        "discord_age_minutes": age,
        "freshness": status,
        "priority": priority,
        "rank": rank,
        "tags": result["tags"],
        "tickers": result["tickers"],
        "source_id": SOURCE_ID,
        "source_claims_verified": False,
        "underlying_event_time_verified": False,
        "trade_signal": False,
        "execution_enabled": False
    }

def main():
    if not INPUT.is_file():
        raise SystemExit("STOP: Intelligence journal missing")

    ignored = (ROOT / ".gitignore").read_text().splitlines()
    if "journal/" not in ignored:
        raise SystemExit("STOP: Journal must be gitignored")

    now = datetime.now(timezone.utc)
    results = {}
    invalid_lines = 0

    with INPUT.open() as f:
        for line in f:
            try:
                r = json.loads(line)
                if str(r.get("author_id")) != SOURCE_ID:
                    continue

                mid = str(r.get("id", ""))
                if not mid or mid == "None":
                    continue

                if mid not in results:
                    results[mid] = prioritize(r, now)
            except (ValueError, KeyError, TypeError):
                invalid_lines += 1

    items = sorted(
        results.values(),
        key=lambda r: (
            -r["rank"],
            r["discord_age_minutes"]
            if r["discord_age_minutes"] is not None
            else float("inf")
        )
    )

    counts = Counter(x["priority"] for x in items)
    recent = sum(
        x["freshness"] == "RECENT_DISCORD_POST"
        for x in items
    )

    print("\n=== JAHVASCRIPT PRIORITY ENGINE ===")
    print("Source: UNUSUAL WHALES DISCORD")
    print("Messages:", len(items))
    print("Recent Discord posts:", recent)
    print("Invalid journal lines:", invalid_lines)

    print("\nRESEARCH PRIORITIES")
    for label in (
        "HIGH_REVIEW", "REVIEW", "BACKGROUND",
        "UNCATEGORIZED", "ARCHIVE_ONLY"
    ):
        print(label + ":", counts[label])

    print("\nTOP 8 RESEARCH ITEMS")
    for r in items[:8]:
        print(
            r["priority"],
            "|", r["message_id"],
            "|", ",".join(r["tickers"]) or "no ticker",
            "|", ",".join(r["tags"]),
            "|", r["freshness"]
        )

    report = {
        "created_utc": now.isoformat(),
        "source": "DISCORD_RESEARCH",
        "source_claims_verified": False,
        "execution_enabled": False,
        "trade_signals_enabled": False,
        "items": items
    }

    os.umask(0o077)
    OUTPUT.parent.mkdir(exist_ok=True)
    OUTPUT.write_text(json.dumps(report, indent=2) + "\n")
    OUTPUT.chmod(0o600)

    print("\nBrief saved: intelligence_brief_latest.json")
    print("PRIORITY ENGINE COMPLETE")
    print("AI REQUESTS: NONE")
    print("ORDER EXECUTION: DISABLED")

if __name__ == "__main__":
    main()
