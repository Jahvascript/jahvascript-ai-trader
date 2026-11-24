"""JahvaScript News Council Runner.
Default: offline preview.
Live mode requires two explicit command-line flags.
No brokerage or order-execution functionality.
"""

import argparse
import json
import os
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent
SOURCE = ROOT / "journal/council_news_preview.json"
PREVIEW = ROOT / "journal/council_news_prompts.json"
RESULTS = ROOT / "journal/council_news_results.json"

MAX_BRIEF_AGE = 10
MAX_PROMPT_CHARS = 6000

ROLES = {
    "openai": (
        "Assess potential relevance to SPY/QQQ and "
        "identify what evidence is missing."
    ),
    "claude": (
        "Independently challenge the reported news, "
        "question its relevance and identify failure modes."
    )
}

SCHEMA = {
    "verdict": "INSUFFICIENT_DATA",
    "thesis": "Unverified news requires independent research.",
    "risk_flags": [
        "Discord claims are not independently verified"
    ],
    "missing_data": [
        "Independent source confirmation",
        "Current validated market data"
    ]
}


def build_prompt(data, role):
    return (
        data["system_instructions"]
        + "\n\n"
        + "ROLE: " + ROLES[role]
        + "\n\n"
        + "Return ONLY valid JSON with these keys:\n"
        + "verdict, thesis, risk_flags, missing_data.\n"
        + "Allowed verdicts: RESEARCH, REJECT, "
        + "INSUFFICIENT_DATA.\n"
        + "RESEARCH means investigate further, NOT trade approval.\n"
        + "thesis must be a string; both lists contain strings.\n"
        + "If evidence is inadequate, use INSUFFICIENT_DATA.\n"
        + "No buy/sell instructions, position sizing, or orders.\n"
        + "\nBEGIN UNTRUSTED NEWS DATA\n"
        + data["user_message"]
        + "\nEND UNTRUSTED NEWS DATA"
    )


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--run", action="store_true",
        help="Request billable AI research"
    )
    parser.add_argument(
        "--confirm-billable", action="store_true",
        help="Explicitly acknowledge API charges"
    )
    args = parser.parse_args()

    if not SOURCE.is_file():
        raise SystemExit("STOP: News preview missing")

    if "journal/" not in (
        ROOT / ".gitignore"
    ).read_text().splitlines():
        raise SystemExit("STOP: Journal not gitignored")

    data = json.loads(SOURCE.read_text())

    if (
        data.get("execution_enabled") is not False
        or data.get("ai_requests_sent") is not False
        or data.get("models_to_review")
            != ["openai", "claude"]
    ):
        raise SystemExit("STOP: Unsafe preview configuration")

    prepared = datetime.fromisoformat(
        data["prepared_utc"]
    )

    if prepared.tzinfo is None:
        raise SystemExit("STOP: Invalid preview timestamp")

    age = (
        datetime.now(timezone.utc) - prepared
    ).total_seconds() / 60

    if age < -2 or age > MAX_BRIEF_AGE:
        raise SystemExit(
            "STOP: Preview is stale. "
            "Refresh the intelligence pipeline."
        )

    news = json.loads(data["user_message"])

    if (
        news.get("purpose") != "market_context_only"
        or news.get("live_market_prices_verified") is not False
        or news.get("underlying_news_events_verified") is not False
        or not 1 <= len(news.get("items", [])) <= 10
    ):
        raise SystemExit("STOP: Unexpected news payload")

    prompts = {
        role: build_prompt(data, role)
        for role in ROLES
    }

    if any(
        len(prompt) > MAX_PROMPT_CHARS
        for prompt in prompts.values()
    ):
        raise SystemExit("STOP: Prompt size limit exceeded")

    # Reuse the original Council response validator.
    from ai_council import validate_review

    validate_review(json.dumps(SCHEMA))

    os.umask(0o077)

    preview = {
        "prepared_utc": datetime.now(timezone.utc).isoformat(),
        "news_items": len(news["items"]),
        "prompts": prompts,
        "response_schema_test": "PASS",
        "untrusted_source": True,
        "execution_enabled": False,
        "ai_requests_sent": False
    }

    PREVIEW.write_text(json.dumps(preview, indent=2) + "\n")
    PREVIEW.chmod(0o600)

    print("\n=== JAHVASCRIPT NEWS COUNCIL RUNNER ===")
    print("News items:", len(news["items"]))
    print("Source preview age:", round(max(age, 0), 1), "min")
    print("OpenAI prompt characters:", len(prompts["openai"]))
    print("Claude prompt characters:", len(prompts["claude"]))
    print("Original Council validator: PASS")
    print("Order execution: DISABLED")

    if not args.run:
        print("\nMODE: OFFLINE PREVIEW")
        print("AI REQUESTS: NONE")
        print("Preview saved: council_news_prompts.json")
        print("\nNEWS RUNNER PREVIEW PASSED")
        return

    if not args.confirm_billable:
        raise SystemExit(
            "STOP: Billable mode requires "
            "--run --confirm-billable"
        )

    # Import existing provider functions only in live mode.
    from council_news_adapter import ask_openai_news as ask_openai, ask_claude_news as ask_claude

    report = {
        "generated_utc": datetime.now(timezone.utc).isoformat(),
        "research_only": True,
        "execution_enabled": False,
        "source_claims_verified": False,
        "reviews": {}
    }

    for role, function in (
        ("openai", ask_openai),
        ("claude", ask_claude)
    ):
        print("Requesting independent", role, "research...")

        try:
            raw = function(prompts[role])

            if isinstance(raw, dict):
                review = validate_review(json.dumps(raw))
            elif isinstance(raw, str):
                review = validate_review(raw)
            else:
                raise TypeError("Unexpected response type")

            report["reviews"][role] = {
                "status": "VALIDATED",
                "review": review
            }

        except Exception as exc:
            report["reviews"][role] = {
                "status": "ERROR",
                "error_type": type(exc).__name__
            }

        # Save after each request, including partial failures.
        RESULTS.write_text(
            json.dumps(report, indent=2) + "\n"
        )
        RESULTS.chmod(0o600)

    print("\n=== COUNCIL RESULTS ===")

    for role, result in report["reviews"].items():
        print(role.upper(), ":", result["status"])

        if result["status"] == "VALIDATED":
            print("Verdict:", result["review"]["verdict"])
            print("Thesis:", result["review"]["thesis"])

    print("\nSaved: council_news_results.json")
    print("COUNCIL RESEARCH COMPLETE")
    print("ORDER EXECUTION: DISABLED")


if __name__ == "__main__":
    main()
