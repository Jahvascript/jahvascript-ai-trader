import os
import json
import discord
from pathlib import Path
from datetime import datetime, timezone
from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent
load_dotenv(ROOT / ".env")
TOKEN = os.getenv("DISCORD_BOT_TOKEN")

if not TOKEN:
    raise SystemExit("Discord bot token missing!")

os.umask(0o077)
JOURNAL = ROOT / "journal"
JOURNAL.mkdir(exist_ok=True)
OUTPUT = JOURNAL / "discord_market_updates.jsonl"

TRUSTED_SENDER_ID = 800519694754644029
seen = set()

if OUTPUT.exists():
    for line in OUTPUT.read_text().splitlines():
        try:
            record = json.loads(line)
            mid = record.get("id")
            if mid is not None:
                seen.add(str(mid))
        except (ValueError, TypeError):
            pass

intents = discord.Intents.default()
intents.message_content = True
client = discord.Client(intents=intents)

def save(message):
    if (
        not message.author.bot
        or message.author.id != TRUSTED_SENDER_ID
        or str(message.id) in seen
    ):
        return

    record = {
        "id": str(message.id),
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "discord_timestamp": message.created_at.isoformat(),
        "author_id": str(message.author.id),
        "author": str(message.author),
        "text": message.content,
        "embeds": [e.to_dict() for e in message.embeds],
        "source_verified": False,
        "sender_id_matched": True,
        "post_age_seconds": max(
            0,
            int(
                (datetime.now(timezone.utc)
                 - message.created_at).total_seconds()
            )
        ),
        "research_only": True,
        "execution_enabled": False
    }

    with OUTPUT.open("a") as f:
        f.write(json.dumps(record) + "\n")

    seen.add(str(message.id))
    print("Saved update:", message.id, flush=True)

@client.event
async def on_ready():
    print("\nJAHVASCRIPT DISCORD CONNECTED")
    print("Bot:", client.user)

    matches = [
        c for g in client.guilds
        if g.name == "JahvaScript Trading Intelligence"
        for c in g.text_channels
        if c.name == "unusual-whales"
    ]

    if len(matches) != 1:
        print("Target channel not found uniquely.")
        await client.close()
        return

    client.target_channel = matches[0]
    print("Channel:", client.target_channel.name)

    async for msg in client.target_channel.history(limit=10):
        save(msg)

    print("LISTENER READY")
    print("Waiting for Discord updates...")
    print("Press Control+C to stop.")

@client.event
async def on_message(message):
    if getattr(client, "target_channel", None):
        if message.channel.id == client.target_channel.id:
            save(message)

client.run(TOKEN, log_handler=None)
