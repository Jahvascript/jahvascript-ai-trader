"""JahvaScript Intelligence Classifier V2.
Read-only local analysis. No AI calls or orders.
"""

import json
import re
from collections import Counter
from pathlib import Path

from market_intelligence_classifier import message_text

ROOT = Path(__file__).resolve().parent
JOURNAL = ROOT / "journal/discord_market_updates.jsonl"
TRUSTED_SENDER = "800519694754644029"

PATTERNS = {
    "MACRO_ECONOMY": (
        r"\b(inflation|consumer prices|cpi|pce|"
        r"interest rates?|federal reserve|fomc|"
        r"jobs report|unemployment|gdp|payrolls?)\b"
    ),
    "CORPORATE_DEVELOPMENT": (
        r"\b(acquires?|acquisition|agreed to buy|"
        r"merger|collaborat(?:es|ion)|partnership|"
        r"earnings|revenue|guidance|ceo|"
        r"cloud partner|outlook)\b"
    ),
    "ANALYST_RATINGS": (
        r"\b(upgraded?|downgraded?|maintained|"
        r"reiterated|price target|analyst rating)\b"
    ),
    "TECHNICAL_ANALYSIS": (
        r"\b(oversold|overbought|bollinger|"
        r"moving average|rsi|macd|vwap|"
        r"breakout|support level|resistance)\b"
    ),
    "OPTIONS_ACTIVITY": (
        r"\b(options?|calls?|puts?|sweep|"
        r"open interest|gamma|premium|0dte|"
        r"options flow|unusual volume)\b"
    ),
    "GOVERNMENT_POLICY": (
        r"\b(subsid(?:y|ies)|tariffs?|sanctions?|"
        r"legislation|industry ministry|regulation)\b"
    ),
    "HOUSING_ECONOMY": (
        r"\b(housing|home sellers|home buyers|"
        r"mortgage|redfin|real estate)\b"
    ),
    "MARKET_DISRUPTION": (
        r"\b(trading halt|halted|circuit breaker|"
        r"exchange outage|trading suspended)\b"
    ),
    "MARKET_UPDATE": (
        r"\b(market open|market close|"
        r"market update|index futures|market breadth)\b"
    )
}


def classify(record):
    raw = message_text(record)

    tags = [
        name for name, pattern in PATTERNS.items()
        if re.search(pattern, raw, re.I)
    ]

    mentioned = set(
        re.findall(r"\$([A-Z]{1,6})\b", raw)
    )

    for ticker in ("SPY", "QQQ"):
        if re.search(r"\b" + ticker + r"\b", raw, re.I):
            mentioned.add(ticker)

    transport = (
        "SOCIAL_REPOST"
        if "Twitter post link:" in raw
        else "DISCORD_POST"
    )

    return {
        "id": str(record["id"]),
        "tags": tags or ["UNCATEGORIZED"],
        "tickers": sorted(mentioned),
        "transport": transport,
        "source_claims_verified": False,
        "research_only": True,
        "execution_enabled": False
    }


def main():
    if not JOURNAL.is_file():
        raise SystemExit("STOP: Journal missing")

    results = []
    seen = set()

    with JOURNAL.open() as f:
        for line in f:
            try:
                record = json.loads(line)
            except json.JSONDecodeError:
                continue

            if str(record.get("author_id")) != TRUSTED_SENDER:
                continue

            mid = str(record.get("id", ""))

            if not mid or mid in seen:
                continue

            seen.add(mid)
            results.append((
                record.get("discord_timestamp", ""),
                classify(record)
            ))

    results.sort(key=lambda item: item[0])

    categories = Counter()
    formats = Counter()

    for _, result in results:
        categories.update(result["tags"])
        formats.update([result["transport"]])

    print("\n=== JAHVASCRIPT INTELLIGENCE V2 ===")
    print("Messages analyzed:", len(results))
    print("\nCATEGORIES")

    for name, count in sorted(categories.items()):
        print(name + ":", count)

    print("\nMESSAGE FORMATS")
    for name, count in sorted(formats.items()):
        print(name + ":", count)

    print("\nLATEST 8 CLASSIFICATIONS")

    for _, result in results[-8:]:
        print(
            result["id"],
            "|", ",".join(result["tags"]),
            "|", ",".join(result["tickers"]) or "no ticker"
        )

    print("\nINTELLIGENCE V2 COMPLETE")
    print("SOURCE CLAIMS: UNVERIFIED")
    print("AI REQUESTS: NONE")
    print("ORDER EXECUTION: DISABLED")


if __name__ == "__main__":
    main()
