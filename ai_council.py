"""JahvaScript AI Council — research only, no broker orders."""

import argparse
import json
import os
from datetime import datetime, timezone
from pathlib import Path

os.umask(0o077)

ROOT = Path(__file__).resolve().parent
JOURNAL = ROOT / "journal"

SYSTEM = (
    "You are an independent options-market research reviewer. "
    "Use ONLY the candidate facts provided. Do not fabricate "
    "indicators, news, real-time prices, or account balances. "
    "Delayed and synthetic quotes are NOT actionable. "
    "Reply ONLY with a JSON object containing: "
    "verdict (RESEARCH, REJECT, or INSUFFICIENT_DATA), "
    "thesis (brief string), risk_flags (array of strings), "
    "and missing_data (array of strings). "
    "Never issue an order command or promise returns."
)

# Synthetic example — NOT actual Webull market data
SAMPLE = {
    "source": "SYNTHETIC_TEST_ONLY",
    "underlying": "SPY",
    "option_symbol": "DEMO_NOT_TRADABLE",
    "option_type": "CALL",
    "bid": "1.00",
    "ask": "1.05",
    "volume": 500,
    "open_interest": 1000,
    "delta": "0.40",
    "implied_volatility": "0.20",
    "quote_age_minutes": 20,
    "execution_enabled": False
}


def load_ai_settings():
    env_file = ROOT / ".env"

    if not env_file.exists():
        return

    allowed = {
        "OPENAI_API_KEY",
        "ANTHROPIC_API_KEY",
        "OPENAI_MODEL",
        "ANTHROPIC_MODEL"
    }

    for line in env_file.read_text().splitlines():
        if "=" not in line or line.lstrip().startswith("#"):
            continue

        key, value = line.split("=", 1)
        key = key.strip()

        if key in allowed:
            os.environ.setdefault(key, value.strip())


def validate_candidate(candidate):
    if not isinstance(candidate, dict):
        raise ValueError("Invalid candidate")

    if candidate.get("source") != "SYNTHETIC_TEST_ONLY":
        raise ValueError("Only synthetic input is enabled")

    if candidate.get("execution_enabled") is not False:
        raise ValueError("Execution must be disabled")


def get_prompt(candidate, role):
    if role == "openai":
        focus = (
            "Assess the potential opportunity and "
            "identify missing evidence."
        )
    else:
        focus = (
            "Independently challenge the setup and "
            "identify ways it could fail."
        )

    return (
        focus
        + "\nCandidate JSON:\n"
        + json.dumps(candidate, sort_keys=True)
    )


def validate_review(raw):
    obj = json.loads(raw)

    allowed = {
        "RESEARCH", "REJECT", "INSUFFICIENT_DATA"
    }

    if (
        not isinstance(obj, dict)
        or obj.get("verdict") not in allowed
    ):
        raise ValueError("Invalid AI verdict")

    if not isinstance(obj.get("thesis"), str):
        raise ValueError("Missing thesis")

    for key in ("risk_flags", "missing_data"):
        values = obj.get(key)

        if (
            not isinstance(values, list)
            or not all(isinstance(v, str) for v in values)
        ):
            raise ValueError("Invalid review data")

    return {
        key: obj[key]
        for key in (
            "verdict",
            "thesis",
            "risk_flags",
            "missing_data"
        )
    }


def ask_openai(prompt):
    from openai import OpenAI

    client = OpenAI(timeout=30.0, max_retries=0)

    response = client.responses.create(
        model=os.environ["OPENAI_MODEL"],
        instructions=SYSTEM,
        input=prompt,
        max_output_tokens=500,
        store=False
    )

    return validate_review(response.output_text)


def ask_claude(prompt):
    from anthropic import Anthropic

    client = Anthropic(timeout=30.0, max_retries=0)

    response = client.messages.create(
        model=os.environ["ANTHROPIC_MODEL"],
        max_tokens=500,
        system=SYSTEM,
        messages=[
            {"role": "user", "content": prompt}
        ]
    )

    raw = "".join(
        block.text
        for block in response.content
        if block.type == "text"
    )

    return validate_review(raw)


def main():
    parser = argparse.ArgumentParser()

    parser.add_argument(
        "--send-to-apis",
        action="store_true",
        help="Make billable requests to both AI providers"
    )

    args = parser.parse_args()

    candidate = SAMPLE
    validate_candidate(candidate)

    prompts = {
        name: get_prompt(candidate, name)
        for name in ("openai", "claude")
    }

    JOURNAL.mkdir(exist_ok=True)

    report = {
        "created_utc": datetime.now(
            timezone.utc
        ).isoformat(),
        "input_source": candidate["source"],
        "order_execution": "DISABLED",
        "api_calls": "NONE",
        "reviews": {},
        "result": "HOLD"
    }

    if args.send_to_apis:
        load_ai_settings()

        required = (
            "OPENAI_API_KEY",
            "ANTHROPIC_API_KEY",
            "OPENAI_MODEL",
            "ANTHROPIC_MODEL"
        )

        missing = [
            key for key in required
            if not os.environ.get(key)
        ]

        if missing:
            raise SystemExit(
                "Missing AI settings: "
                + ", ".join(missing)
            )

        report["reviews"]["openai"] = ask_openai(
            prompts["openai"]
        )

        report["reviews"]["claude"] = ask_claude(
            prompts["claude"]
        )

        report["api_calls"] = "TWO"
        report["result"] = "RESEARCH_ONLY"

    else:
        report["result"] = "WAITING_FOR_AI_KEYS"

    path = JOURNAL / "last_council_report.json"

    path.write_text(
        json.dumps(report, indent=2) + "\n"
    )

    print("\n=== JAHVASCRIPT AI COUNCIL ===")
    print("Input:", report["input_source"])
    print("API calls:", report["api_calls"])
    print("Decision:", report["result"])
    print("Report: journal/last_council_report.json")
    print("ORDER EXECUTION: DISABLED")


if __name__ == "__main__":
    main()
