"""Claude-only archived briefing retest.
Default: offline. No brokerage functionality.
"""

import argparse
import hashlib
import json
import os
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent
PROMPTS = ROOT / "journal/council_news_prompts.json"
PREVIOUS = ROOT / "journal/council_news_results.json"
OUTPUT = ROOT / "journal/claude_news_retest.json"

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--run", action="store_true")
    parser.add_argument("--confirm-billable", action="store_true")
    args = parser.parse_args()

    if "journal/" not in (ROOT / ".gitignore").read_text().splitlines():
        raise SystemExit("STOP: Journal is not gitignored")

    if not PROMPTS.is_file() or not PREVIOUS.is_file():
        raise SystemExit("STOP: Original Council test files missing")

    prompts = json.loads(PROMPTS.read_text())
    previous = json.loads(PREVIOUS.read_text())

    if (
        prompts.get("execution_enabled") is not False
        or previous.get("execution_enabled") is not False
        or previous.get("reviews", {}).get("openai", {}).get("status")
            != "VALIDATED"
    ):
        raise SystemExit("STOP: Original OpenAI result not validated")

    prompt = prompts.get("prompts", {}).get("claude")

    if not isinstance(prompt, str) or not 1 <= len(prompt) <= 6000:
        raise SystemExit("STOP: Invalid saved Claude prompt")

    fingerprint = hashlib.sha256(
        prompt.encode("utf-8")
    ).hexdigest()[:16]

    print("\n=== JAHVASCRIPT CLAUDE-ONLY RETEST ===")
    print("Original OpenAI result: PRESERVED")
    print("Saved Claude prompt: FOUND")
    print("Prompt characters:", len(prompt))
    print("Prompt fingerprint:", fingerprint)
    print("News context: ARCHIVED / NOT CURRENT")
    print("Order execution: DISABLED")

    if not args.run:
        print("\nMODE: OFFLINE READINESS CHECK")
        print("API REQUESTS: NONE")
        print("\nCLAUDE RETEST READY")
        return

    if not args.confirm_billable:
        raise SystemExit("STOP: Explicit billing confirmation required")

    # Exactly one provider call; no OpenAI request.
    from council_news_adapter import ask_claude_news

    report = {
        "tested_utc": datetime.now(timezone.utc).isoformat(),
        "provider": "claude",
        "archived_prompt_fingerprint": fingerprint,
        "news_is_current": False,
        "research_only": True,
        "execution_enabled": False,
        "status": "ERROR"
    }

    try:
        review = ask_claude_news(prompt)
        report["status"] = "VALIDATED"
        report["review"] = review
    except Exception as exc:
        report["error_type"] = type(exc).__name__

    os.umask(0o077)
    OUTPUT.write_text(json.dumps(report, indent=2) + "\n")
    OUTPUT.chmod(0o600)

    print("\nCLAUDE STATUS:", report["status"])

    if report["status"] == "VALIDATED":
        print("Verdict:", report["review"]["verdict"])
        print("Thesis:", report["review"]["thesis"])
    else:
        print("Error type:", report["error_type"])

    print("Original OpenAI result: UNCHANGED")
    print("Saved: claude_news_retest.json")
    print("Order execution: DISABLED")
    print("\nCLAUDE RETEST COMPLETE")

if __name__ == "__main__":
    main()
