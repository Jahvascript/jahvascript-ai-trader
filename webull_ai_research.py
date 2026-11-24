"""Webull delayed-data AI research.
No broker connection. No order execution.
"""

import argparse
import json
import os
from datetime import datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation
from pathlib import Path

from ai_council import (
    SYSTEM, get_prompt, load_ai_settings, validate_review
)
from first_ai_test import SCHEMA

ROOT = Path(__file__).resolve().parent
CANDIDATES = ROOT / "journal/webull_candidates_latest.json"

os.umask(0o077)


def number(value):
    try:
        n = Decimal(str(value))
        return n if n.is_finite() else None
    except (ValueError, TypeError, InvalidOperation):
        return None


def load_candidates():
    data = json.loads(CANDIDATES.read_text())

    if (
        data.get("source") != "WEBULL_SANDBOX_DELAYED"
        or data.get("execution_enabled") is not False
    ):
        raise ValueError("Research-only source required")

    created = datetime.fromisoformat(
        data["created_utc"].replace("Z", "+00:00")
    )

    if created.tzinfo is None:
        raise ValueError("Unknown snapshot timezone")

    now = datetime.now(timezone.utc)

    if not (
        timedelta(0)
        <= now - created
        <= timedelta(hours=96)
    ):
        raise ValueError(
            "Research file expired. Rerun market_bridge.py"
        )

    selected = []
    seen = set()

    for candidate in data.get("candidates", []):
        if not isinstance(candidate, dict):
            continue

        ticker = candidate.get("underlying")

        if ticker not in ("SPY", "QQQ") or ticker in seen:
            continue

        if (
            candidate.get("source") != "WEBULL_SANDBOX_DELAYED"
            or candidate.get("execution_enabled") is not False
            or candidate.get("research_only") is not True
        ):
            continue

        bid = number(candidate.get("bid"))
        ask = number(candidate.get("ask"))
        volume = number(candidate.get("volume"))
        interest = number(candidate.get("open_interest"))

        if any(x is None for x in (bid, ask, volume, interest)):
            continue

        if (
            bid <= 0
            or ask < bid
            or volume < 100
            or interest < 100
        ):
            continue

        spread = (ask - bid) / ((ask + bid) / 2) * 100

        if spread > 3:
            continue

        try:
            quote_time = datetime.fromtimestamp(
                int(candidate["quote_time"]) / 1000,
                timezone.utc
            )
        except (KeyError, TypeError, ValueError, OverflowError):
            continue

        age = now - quote_time

        if not (
            timedelta(0)
            <= age
            <= timedelta(hours=96)
        ):
            continue

        prepared = dict(candidate)
        prepared["quote_age_minutes_now"] = round(
            age.total_seconds() / 60, 1
        )
        prepared["market_data_status"] = (
            "DELAYED_OR_STALE_NOT_REALTIME"
        )
        prepared["research_only"] = True
        prepared["execution_enabled"] = False

        selected.append(prepared)
        seen.add(ticker)

        if len(selected) == 2:
            break

    return selected


def ask_openai(candidate):
    from openai import OpenAI

    client = OpenAI(timeout=35.0, max_retries=0)

    response = client.responses.create(
        model=os.environ["OPENAI_MODEL"],
        instructions=SYSTEM,
        input=get_prompt(candidate, "openai"),
        reasoning={"effort": "none"},
        text={
            "format": {
                "type": "json_schema",
                "name": "options_research",
                "strict": True,
                "schema": SCHEMA
            }
        },
        max_output_tokens=600,
        store=False
    )

    if response.status != "completed":
        raise ValueError("Incomplete OpenAI response")

    review = validate_review(response.output_text)

    usage = None
    if response.usage:
        usage = {
            "input": response.usage.input_tokens,
            "output": response.usage.output_tokens
        }

    return review, usage


def ask_claude(candidate):
    from anthropic import Anthropic

    client = Anthropic(timeout=35.0, max_retries=0)

    response = client.messages.create(
        model=os.environ["ANTHROPIC_MODEL"],
        max_tokens=1200,
        system=SYSTEM,
        messages=[{
            "role": "user",
            "content": get_prompt(candidate, "claude")
        }],
        output_config={
            "format": {
                "type": "json_schema",
                "schema": SCHEMA
            }
        }
    )

    if response.stop_reason != "end_turn":
        raise ValueError("Incomplete Claude response")

    raw = "".join(
        block.text
        for block in response.content
        if block.type == "text"
    )

    review = validate_review(raw)

    return review, {
        "input": response.usage.input_tokens,
        "output": response.usage.output_tokens
    }


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--run",
        action="store_true",
        help="Authorize billable AI research requests"
    )
    args = parser.parse_args()

    candidates = load_candidates()

    print("\n=== JAHVASCRIPT MARKET AI COUNCIL ===")
    print("Source: WEBULL SANDBOX DELAYED")
    print("Broker orders: DISABLED")
    print("Research candidates:", len(candidates))

    for candidate in candidates:
        print(
            candidate["underlying"],
            "|", candidate["option_symbol"],
            "| quote age:",
            candidate["quote_age_minutes_now"],
            "minutes"
        )

    if not candidates:
        print("NO CANDIDATES — REFRESH MARKET BRIDGE")
        return

    if not args.run:
        print("\nDRY RUN — NO AI REQUESTS")
        print(
            "Potential billable requests:",
            len(candidates) * 2
        )
        return

    # Prevent unnecessary paid analysis of stale quotes.
    # This is a research gate, NOT live-trading authorization.
    from datetime import time
    from zoneinfo import ZoneInfo

    now = datetime.now(timezone.utc)
    eastern = now.astimezone(ZoneInfo("America/New_York"))

    if not (
        eastern.weekday() < 5
        and time(9, 30) <= eastern.time() < time(16, 0)
    ):
        print("BLOCKED: Outside regular weekday market hours")
        print("AI REQUESTS: NONE")
        return

    source = json.loads(CANDIDATES.read_text())
    created = datetime.fromisoformat(
        source["created_utc"].replace("Z", "+00:00")
    )

    if created.tzinfo is None:
        print("BLOCKED: Unknown source timezone")
        return

    file_age = (now - created).total_seconds()

    if not 0 <= file_age <= 600:
        print("BLOCKED: Research file older than 10 minutes")
        print("Refresh with python market_bridge.py")
        print("AI REQUESTS: NONE")
        return

    if any(
        not 0 <= c["quote_age_minutes_now"] <= 60
        for c in candidates
    ):
        print("BLOCKED: Quote older than 60 minutes")
        print("AI REQUESTS: NONE")
        return

    print("RESEARCH FRESHNESS GATE: PASS")
    print("LIVE TRADING: STILL DISABLED")

    load_ai_settings()

    required = (
        "OPENAI_API_KEY",
        "ANTHROPIC_API_KEY",
        "OPENAI_MODEL",
        "ANTHROPIC_MODEL"
    )

    if any(not os.environ.get(k) for k in required):
        raise SystemExit("Missing AI API configuration")

    report = {
        "created_utc": datetime.now(
            timezone.utc
        ).isoformat(),
        "source": "WEBULL_SANDBOX_DELAYED",
        "mode": "RESEARCH_ONLY",
        "order_execution": "DISABLED",
        "trading_eligibility": "NOT_AUTHORIZED",
        "results": []
    }

    for candidate in candidates:
        print(
            "\nREVIEWING:",
            candidate["option_symbol"]
        )

        item = {
            "candidate": candidate,
            "providers": {},
            "comparison": "INCOMPLETE",
            "trading_eligibility": "NOT_AUTHORIZED"
        }

        for name, function in (
            ("openai", ask_openai),
            ("claude", ask_claude)
        ):
            try:
                review, tokens = function(candidate)

                item["providers"][name] = {
                    "status": "OK",
                    "review": review,
                    "tokens": tokens
                }

                print(
                    name.upper(),
                    "OK |",
                    review["verdict"]
                )

            except Exception as exc:
                item["providers"][name] = {
                    "status": "ERROR",
                    "error_type": type(exc).__name__
                }

                print(
                    name.upper(),
                    "ERROR |",
                    type(exc).__name__
                )

        a = item["providers"].get("openai", {})
        b = item["providers"].get("claude", {})

        if (
            a.get("status") == "OK"
            and b.get("status") == "OK"
        ):
            same = (
                a["review"]["verdict"]
                == b["review"]["verdict"]
            )

            item["comparison"] = (
                "SAME_VERDICT"
                if same else "DIFFERENT_VERDICT"
            )

        print("Council:", item["comparison"])
        print("Trading: NOT AUTHORIZED")

        report["results"].append(item)

    journal = ROOT / "journal"
    journal.mkdir(mode=0o700, exist_ok=True)

    filename = (
        "webull_ai_research_"
        + datetime.now(timezone.utc).strftime(
            "%Y%m%dT%H%M%S%fZ"
        )
        + ".json"
    )

    path = journal / filename

    with path.open("x") as f:
        json.dump(report, f, indent=2)
        f.write("\n")

    path.chmod(0o600)

    print("\nJournal:", path.relative_to(ROOT))
    print("RESEARCH COMPLETE")
    print("ORDER EXECUTION: DISABLED")


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        print(
            "RESEARCH STOPPED:",
            type(exc).__name__
        )
        if isinstance(exc, ValueError):
            print(str(exc))
