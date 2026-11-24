"""JahvaScript Priority Audit.
Local inspection only. No AI or trading requests.
"""

import json
import re
from pathlib import Path

from market_intelligence_classifier import message_text

ROOT = Path(__file__).resolve().parent
JOURNAL = ROOT / "journal/discord_market_updates.jsonl"
BRIEF = ROOT / "journal/intelligence_brief_latest.json"
SOURCE_ID = "800519694754644029"

if not JOURNAL.exists() or not BRIEF.exists():
    raise SystemExit("STOP: Required journal or brief missing")

brief = json.loads(BRIEF.read_text())
assert brief["execution_enabled"] is False

messages = {}

with JOURNAL.open() as f:
    for line in f:
        try:
            record = json.loads(line)
        except json.JSONDecodeError:
            continue

        if str(record.get("author_id")) != SOURCE_ID:
            continue

        messages[str(record.get("id"))] = record


def preview(message_id):
    record = messages.get(str(message_id))

    if not record:
        return "[MESSAGE NOT FOUND]"

    value = message_text(record)

    value = re.sub(
        r"https?://[^\s)]+",
        "[LINK]",
        value
    )

    value = re.sub(r"\s+", " ", value).strip()

    return value[:240]


items = brief["items"]

print("\n=== JAHVASCRIPT PRIORITY AUDIT ===")
print("Brief messages:", len(items))
print("Source claims: UNVERIFIED\n")

selected = [
    item for item in items
    if item["priority"] in ("HIGH_REVIEW", "REVIEW")
]

print("--- HIGH / REVIEW ITEMS ---")

for item in selected[:12]:
    print("\nPriority:", item["priority"])
    print("Age:", item["discord_age_minutes"], "minutes")
    print("Category:", ",".join(item["tags"]))
    print("Tickers:", ",".join(item["tickers"]) or "none")
    print("Message:", preview(item["message_id"]))

uncategorized = [
    item for item in items
    if item["priority"] == "UNCATEGORIZED"
]

uncategorized.sort(
    key=lambda item: (
        item["discord_age_minutes"]
        if item["discord_age_minutes"] is not None
        else float("inf")
    )
)

print("\n--- UNCATEGORIZED SAMPLES ---")

for item in uncategorized[:6]:
    print("\nAge:", item["discord_age_minutes"], "minutes")
    print("Message:", preview(item["message_id"]))

print("\nPRIORITY AUDIT COMPLETE")
print("AI REQUESTS: NONE")
print("ORDER EXECUTION: DISABLED")
