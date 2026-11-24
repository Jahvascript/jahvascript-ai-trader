"""JahvaScript intelligence classifier.
Local research only. No AI, Discord, or trading requests.
"""
import json
import re
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

SOURCE_ID = "800519694754644029"
ROOT = Path(__file__).resolve().parent
JOURNAL = ROOT / "journal/discord_market_updates.jsonl"

RULES = {
    "ECONOMIC_NEWS": (
        r"\b(cpi|pce|fomc|federal reserve|"
        r"fed meeting|powell|inflation|tariffs?|"
        r"nonfarm payrolls|jobs report|gdp)\b"
    ),
    "OPTIONS_ACTIVITY": (
        r"\b(options?|calls?|puts?|sweep|"
        r"open interest|option flow|premium|"
        r"unusual volume|gamma|0dte)\b"
    ),
    "MARKET_DISRUPTION": (
        r"\b(trading halt|halted|trading suspended|"
        r"exchange outage|circuit breaker)\b"
    ),
    "MARKET_UPDATE": (
        r"\b(market open|market close|"
        r"market update|index futures|"
        r"market breadth|stocks rally|stocks fall)\b"
    )
}

def message_text(record):
    parts = [str(record.get("text") or "")]

    for embed in record.get("embeds") or []:
        if not isinstance(embed, dict):
            continue

        parts.extend([
            str(embed.get("title") or ""),
            str(embed.get("description") or "")
        ])

        for field in embed.get("fields") or []:
            if isinstance(field, dict):
                parts.extend([
                    str(field.get("name") or ""),
                    str(field.get("value") or "")
                ])

    return " ".join(parts)

def classify(record):
    content = message_text(record)
    tags = [
        name for name, pattern in RULES.items()
        if re.search(pattern, content, re.I)
    ]

    tickers = [
        ticker for ticker in ("SPY", "QQQ")
        if re.search(r"\b" + ticker + r"\b", content, re.I)
    ]

    try:
        timestamp = datetime.fromisoformat(
            record["discord_timestamp"]
        )
        age = (
            datetime.now(timezone.utc) - timestamp
        ).total_seconds() / 60
    except (KeyError, ValueError, TypeError):
        age = None

    return {
        "id": str(record.get("id")),
        "tags": tags or ["UNCATEGORIZED"],
        "tickers": tickers,
        "discord_post_age_minutes": (
            round(max(0, age), 1)
            if age is not None else None
        ),
        "source_claims_verified": False,
        "research_only": True,
        "execution_enabled": False
    }

def main():
    if not JOURNAL.exists():
        raise SystemExit("STOP: Discord journal missing")

    records = []
    ids = set()

    for line in JOURNAL.read_text().splitlines():
        try:
            record = json.loads(line)
        except json.JSONDecodeError:
            continue

        if str(record.get("author_id")) != SOURCE_ID:
            continue

        mid = str(record.get("id"))

        if mid in ids or mid == "None":
            continue

        ids.add(mid)
        records.append(classify(record))

    counts = Counter()
    tickers = Counter()

    for record in records:
        counts.update(record["tags"])
        tickers.update(record["tickers"])

    print("\n=== JAHVASCRIPT INTELLIGENCE ===")
    print("STEP 15F: MESSAGE CLASSIFICATION")
    print("Source filter: ACTIVE")
    print("Records analyzed:", len(records))

    print("\nMESSAGE CATEGORIES")
    for name, count in sorted(counts.items()):
        print(f"{name}: {count}")

    print("\nTICKER MENTIONS")
    for ticker in ("SPY", "QQQ"):
        print(ticker + ":", tickers[ticker])

    print("\nLAST 5 JOURNAL RECORDS")
    for record in records[-5:]:
        print(
            record["id"],
            "|", ",".join(record["tags"]),
            "| tickers:", ",".join(record["tickers"]) or "none",
            "| age:", record["discord_post_age_minutes"], "min"
        )

    print("\nCLASSIFICATION COMPLETE")
    print("SOURCE CLAIMS: UNVERIFIED")
    print("AI REQUESTS: NONE")
    print("ORDER EXECUTION: DISABLED")

if __name__ == "__main__":
    main()
