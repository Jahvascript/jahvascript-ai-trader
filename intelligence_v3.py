"""JahvaScript Headline Intelligence V3.
Local research only. No network, AI, or trading.
"""
import json
import os
import re
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

from market_intelligence_classifier import message_text

ROOT = Path(__file__).resolve().parent
INPUT = ROOT / "journal/discord_market_updates.jsonl"
OUTPUT = ROOT / "journal/intelligence_v3_latest.json"
SENDER = "800519694754644029"

os.umask(0o077)

RULES = {
    "MARKET_DISRUPTION":
        r"\b(trading halt|circuit breaker|exchange outage)\b",
    "GEOPOLITICS":
        r"\b(kremlin|russia|ukraine|peace talks|"
        r"military conflict|ceasefire|sanctions)\b",
    "ECONOMIC_NEWS":
        r"\b(cpi|pce|inflation|consumer prices|"
        r"jobs report|fomc|fed rate|treasury yields)\b",
    "GOVERNMENT_POLICY":
        r"\b(tariff|subsidy|subsidies|trade policy)\b",
    "EARNINGS_EVENT":
        r"\b(earnings|reports? after close|"
        r"reports? before open|guidance)\b",
    "OPTIONS_ACTIVITY":
        r"\b(options flow|option sweep|0dte|"
        r"open interest|taker (buy|sell) flow)\b",
    "TECHNICAL_ANALYSIS":
        r"\b(vwap|moving averages?|oversold|"
        r"overbought|bollinger|breakout|rsi)\b",
    "CORPORATE_NEWS":
        r"\b(acquisition|agreed to buy|merger|"
        r"collaborates|partnership|unveiled)\b"
}

NOISE = (
    r"\b(early voting|go vote|mail-in ballot|"
    r"vote at the polls|voter registration|"
    r"campaign rally)\b"
)

MAJOR = {
    "NVDA", "AAPL", "MSFT", "AMZN",
    "META", "GOOG", "GOOGL",
    "TSLA", "AVGO"
}


def split_headlines(text):
    # Discord posts can contain several timestamped headlines.
    parts = re.split(
        r"<t:\d+:[A-Za-z]>\s*-\s*",
        text
    )
    return [x.strip() for x in parts if x.strip()]


def analyze(headline):
    labels = [
        name for name, pattern in RULES.items()
        if re.search(pattern, headline, re.I)
    ]

    tickers = set(
        re.findall(r"\$([A-Z]{1,6})\b", headline)
    )

    for symbol in ("SPY", "QQQ"):
        if re.search(r"\b" + symbol + r"\b", headline, re.I):
            tickers.add(symbol)

    political_noise = bool(
        re.search(NOISE, headline, re.I)
    )

    if political_noise and not labels:
        priority = "FILTERED_NOISE"
    elif "MARKET_DISRUPTION" in labels:
        priority = "HIGH_REVIEW"
    elif tickers & {"SPY", "QQQ"}:
        priority = "REVIEW"
    elif (
        set(labels) & {
            "GEOPOLITICS", "ECONOMIC_NEWS",
            "GOVERNMENT_POLICY", "EARNINGS_EVENT"
        }
    ):
        priority = "REVIEW"
    elif tickers & MAJOR:
        priority = "BACKGROUND"
    elif labels:
        priority = "BACKGROUND"
    else:
        priority = "UNCATEGORIZED"

    # Historical inflation statistics are not new CPI releases.
    if re.search(
        r"consumer prices.*since covid", headline, re.I
    ):
        priority = "BACKGROUND"

    return priority, labels, sorted(tickers)


def main():
    if not INPUT.is_file():
        raise SystemExit("STOP: Discord journal missing")

    if "journal/" not in (
        ROOT / ".gitignore"
    ).read_text().splitlines():
        raise SystemExit("STOP: Journal not gitignored")

    now = datetime.now(timezone.utc)
    results = []
    seen = set()

    with INPUT.open() as f:
        for line in f:
            try:
                msg = json.loads(line)
                if str(msg.get("author_id")) != SENDER:
                    continue

                mid = str(msg.get("id"))
                if mid in seen:
                    continue
                seen.add(mid)

                stamp = datetime.fromisoformat(
                    msg["discord_timestamp"]
                )
                if stamp.tzinfo is None:
                    continue

                age = (now - stamp).total_seconds() / 60
                if age < -2:
                    continue

                for number, headline in enumerate(
                    split_headlines(message_text(msg))
                ):
                    priority, labels, tickers = analyze(
                        headline
                    )

                    if age > 60:
                        priority = "ARCHIVE_ONLY"

                    results.append({
                        "message_id": mid,
                        "headline_index": number,
                        "discord_age_minutes": round(
                            max(0, age), 1
                        ),
                        "priority": priority,
                        "categories": labels,
                        "tickers": tickers,
                        "headline": headline[:1200],
                        "source_claims_verified": False,
                        "event_time_verified": False,
                        "trade_signal": False,
                        "execution_enabled": False
                    })
            except (ValueError, KeyError, TypeError):
                continue

    counts = Counter(
        item["priority"] for item in results
    )

    print("\n=== JAHVASCRIPT INTELLIGENCE V3 ===")
    print("Messages read:", len(seen))
    print("Individual headlines:", len(results))

    print("\nPRIORITIES")
    for name, count in sorted(counts.items()):
        print(name + ":", count)

    print("\nQQQ / SPY HEADLINE EXAMPLES")
    matched = [
        x for x in results
        if set(x["tickers"]) & {"SPY", "QQQ"}
    ]
    for item in matched[-3:]:
        print(
            item["priority"],
            "|", ",".join(item["tickers"]),
            "|", item["headline"][:150]
        )

    print("\nFILTERED NOISE EXAMPLES")
    for item in [
        x for x in results
        if x["priority"] == "FILTERED_NOISE"
    ][:3]:
        print(item["headline"][:130])

    report = {
        "generated_utc": now.isoformat(),
        "source": "DISCORD_RESEARCH",
        "source_claims_verified": False,
        "trade_signals_enabled": False,
        "execution_enabled": False,
        "items": results
    }

    OUTPUT.parent.mkdir(exist_ok=True)
    OUTPUT.write_text(
        json.dumps(report, indent=2) + "\n"
    )
    OUTPUT.chmod(0o600)

    print("\nINTELLIGENCE V3 COMPLETE")
    print("AI REQUESTS: NONE")
    print("ORDER EXECUTION: DISABLED")


if __name__ == "__main__":
    main()
