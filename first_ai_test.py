"""Synthetic-only AI Council test. No broker access."""

import argparse
import json
import os
from datetime import datetime, timezone
from pathlib import Path

from ai_council import (
    SAMPLE, SYSTEM, get_prompt,
    load_ai_settings, validate_review
)

ROOT = Path(__file__).resolve().parent

SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "verdict": {
            "type": "string",
            "enum": [
                "RESEARCH", "REJECT", "INSUFFICIENT_DATA"
            ]
        },
        "thesis": {"type": "string"},
        "risk_flags": {
            "type": "array",
            "items": {"type": "string"}
        },
        "missing_data": {
            "type": "array",
            "items": {"type": "string"}
        }
    },
    "required": [
        "verdict", "thesis",
        "risk_flags", "missing_data"
    ]
}


def openai_review():
    from openai import OpenAI

    client = OpenAI(
        api_key=os.environ["OPENAI_API_KEY"],
        timeout=35.0,
        max_retries=0
    )

    response = client.responses.create(
        model=os.environ["OPENAI_MODEL"],
        instructions=SYSTEM,
        input=get_prompt(SAMPLE, "openai"),
        reasoning={"effort": "none"},
        text={
            "format": {
                "type": "json_schema",
                "name": "trading_research_review",
                "strict": True,
                "schema": SCHEMA
            }
        },
        max_output_tokens=400,
        store=False
    )

    if response.status != "completed" or not response.output_text:
        raise ValueError("Incomplete OpenAI response")

    usage = response.usage
    tokens = {
        "input": usage.input_tokens,
        "output": usage.output_tokens
    } if usage else None

    return validate_review(response.output_text), tokens


def claude_review():
    from anthropic import Anthropic

    client = Anthropic(
        api_key=os.environ["ANTHROPIC_API_KEY"],
        timeout=35.0,
        max_retries=0
    )

    response = client.messages.create(
        model=os.environ["ANTHROPIC_MODEL"],
        max_tokens=1200,
        system=SYSTEM,
        messages=[
            {
                "role": "user",
                "content": get_prompt(SAMPLE, "claude")
            }
        ],
        output_config={
            "format": {
                "type": "json_schema",
                "schema": SCHEMA
            }
        }
    )

    if response.stop_reason != "end_turn":
        raise ValueError(
            f"Claude stop_reason={response.stop_reason}; "
            f"output_tokens={response.usage.output_tokens}"
        )

    raw = "".join(
        part.text
        for part in response.content
        if part.type == "text"
    )

    if not raw:
        raise ValueError("Empty Claude response")

    return validate_review(raw), {
        "input": response.usage.input_tokens,
        "output": response.usage.output_tokens
    }


def main():
    parser = argparse.ArgumentParser()

    parser.add_argument(
        "--run",
        action="store_true",
        help="Allow two billable AI API requests"
    )

    args = parser.parse_args()
    load_ai_settings()

    required = [
        "OPENAI_API_KEY",
        "ANTHROPIC_API_KEY",
        "OPENAI_MODEL",
        "ANTHROPIC_MODEL"
    ]

    missing = [
        key for key in required
        if not os.environ.get(key)
    ]

    if missing:
        raise SystemExit(
            "Missing configuration: " + ", ".join(missing)
        )

    if (
        SAMPLE.get("source") != "SYNTHETIC_TEST_ONLY"
        or SAMPLE.get("execution_enabled") is not False
    ):
        raise SystemExit("BLOCKED: Synthetic test only")

    print("\n=== JAHVASCRIPT AI COUNCIL ===")
    print("Input: SYNTHETIC_TEST_ONLY")
    print("Order execution: DISABLED")

    if not args.run:
        print("DRY RUN: No API calls made.")
        print("Add --run to make two billable requests.")
        return

    report = {
        "created_utc": datetime.now(
            timezone.utc
        ).isoformat(),
        "input": SAMPLE,
        "mode": "SYNTHETIC_RESEARCH_ONLY",
        "orders_enabled": False,
        "providers": {},
        "overall": "HOLD"
    }

    for name, function in (
        ("openai", openai_review),
        ("claude", claude_review)
    ):
        print(f"\n{name.upper()} request...")

        try:
            review, usage = function()

            report["providers"][name] = {
                "status": "OK",
                "review": review,
                "tokens": usage
            }

            print("Status: OK")
            print("Verdict:", review["verdict"])
            print(
                "Risk flags:",
                "; ".join(review["risk_flags"])
                or "None returned"
            )

        except Exception as exc:
            # Never print raw SDK errors or HTTP headers.
            report["providers"][name] = {
                "status": "ERROR",
                "error_type": type(exc).__name__
            }

            print("Status: ERROR")
            print("Error type:", type(exc).__name__)

    a = report["providers"].get("openai", {})
    b = report["providers"].get("claude", {})

    if (
        a.get("status") == "OK"
        and b.get("status") == "OK"
    ):
        same = (
            a["review"]["verdict"]
            == b["review"]["verdict"]
        )

        report["agreement"] = (
            "SAME_VERDICT" if same else "DISAGREEMENT"
        )
    else:
        report["agreement"] = "INCOMPLETE"

    print("\nCouncil comparison:", report["agreement"])

    folder = ROOT / "journal"
    folder.mkdir(mode=0o700, exist_ok=True)

    filename = (
        "first_ai_test_"
        + datetime.now(timezone.utc).strftime(
            "%Y%m%dT%H%M%SZ"
        )
        + ".json"
    )

    path = folder / filename

    with path.open("x") as f:
        json.dump(report, f, indent=2)
        f.write("\n")

    path.chmod(0o600)

    print("Journal:", path.relative_to(ROOT))
    print("ORDER EXECUTION: DISABLED")


if __name__ == "__main__":
    main()
