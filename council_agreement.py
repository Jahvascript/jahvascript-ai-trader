"""JahvaScript AI Council agreement analysis.
Archived research only. No API calls or trading.
"""

import hashlib
import json
import os
from datetime import datetime, timezone
from pathlib import Path

from ai_council import validate_review

ROOT = Path(__file__).resolve().parent
JOURNAL = ROOT / "journal"

OPENAI_FILE = JOURNAL / "council_news_results.json"
CLAUDE_FILE = JOURNAL / "claude_news_retest.json"
PROMPTS_FILE = JOURNAL / "council_news_prompts.json"
OUTPUT = JOURNAL / "council_agreement_latest.json"

def main():
    if "journal/" not in (
        ROOT / ".gitignore"
    ).read_text().splitlines():
        raise SystemExit("STOP: Journal not gitignored")

    original = json.loads(OPENAI_FILE.read_text())
    retest = json.loads(CLAUDE_FILE.read_text())
    prompts = json.loads(PROMPTS_FILE.read_text())

    if (
        original.get("execution_enabled") is not False
        or retest.get("execution_enabled") is not False
        or retest.get("news_is_current") is not False
    ):
        raise SystemExit("STOP: Unsafe or unexpected report")

    claude_prompt = prompts["prompts"]["claude"]
    fingerprint = hashlib.sha256(
        claude_prompt.encode("utf-8")
    ).hexdigest()[:16]

    if fingerprint != retest.get(
        "archived_prompt_fingerprint"
    ):
        raise SystemExit("STOP: Claude prompt mismatch")

    a = original["reviews"]["openai"]
    b = retest

    if (
        a.get("status") != "VALIDATED"
        or b.get("status") != "VALIDATED"
    ):
        raise SystemExit("STOP: Both reviews must be valid")

    openai = validate_review(
        json.dumps(a["review"])
    )
    claude = validate_review(
        json.dumps(b["review"])
    )

    same_verdict = (
        openai["verdict"] == claude["verdict"]
    )

    if "REJECT" in (
        openai["verdict"], claude["verdict"]
    ):
        council_status = "REJECT_RESEARCH_CASE"
    elif "INSUFFICIENT_DATA" in (
        openai["verdict"], claude["verdict"]
    ):
        council_status = "INSUFFICIENT_EVIDENCE"
    else:
        council_status = "FURTHER_RESEARCH_ONLY"

    report = {
        "generated_utc":
            datetime.now(timezone.utc).isoformat(),
        "historical_research_only": True,
        "source_claims_verified": False,
        "verdicts_agree": same_verdict,
        "council_status": council_status,
        "openai": openai,
        "claude": claude,
        "execution_enabled": False,
        "trade_authorization": False
    }

    os.umask(0o077)
    OUTPUT.write_text(
        json.dumps(report, indent=2) + "\n"
    )
    OUTPUT.chmod(0o600)

    print("\n=== JAHVASCRIPT AI COUNCIL ===")
    print("STEP 15H.1: AGREEMENT ANALYSIS")

    for name, review in (
        ("OPENAI", openai),
        ("CLAUDE", claude)
    ):
        print("\n" + name)
        print("Verdict:", review["verdict"])
        print("Thesis:", review["thesis"])

        print("Risk flags:")
        for risk in review["risk_flags"]:
            print(" -", risk)

        print("Missing evidence:")
        for missing in review["missing_data"]:
            print(" -", missing)

    print("\nCOUNCIL COMPARISON")
    print("Verdicts agree:", same_verdict)
    print("Council status:", council_status)
    print("Semantic agreement: NOT AUTOMATICALLY VERIFIED")
    print("Data: ARCHIVED / UNVERIFIED")
    print("Report: council_agreement_latest.json")

    print("\nCOUNCIL AGREEMENT ANALYSIS PASSED")
    print("AI REQUESTS: NONE")
    print("ORDER EXECUTION: DISABLED")


if __name__ == "__main__":
    main()
