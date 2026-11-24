"""JahvaScript Council Synthesis.
Deterministic research summary. No AI or trading calls.
"""

import json
import os
from datetime import datetime, timezone
from pathlib import Path

from ai_council import validate_review

ROOT = Path(__file__).resolve().parent
SOURCE = ROOT / "journal/council_agreement_latest.json"
OUTPUT = ROOT / "journal/council_synthesis_latest.json"


def attributed_items(reviews, field):
    items = []

    for provider, review in reviews.items():
        for value in review[field]:
            items.append({
                "source_model": provider,
                "statement": value
            })

    return items


def main():
    if "journal/" not in (
        ROOT / ".gitignore"
    ).read_text().splitlines():
        raise SystemExit("STOP: Journal not gitignored")

    if not SOURCE.is_file():
        raise SystemExit("STOP: Agreement report missing")

    data = json.loads(SOURCE.read_text())

    if (
        data.get("execution_enabled") is not False
        or data.get("trade_authorization") is not False
        or data.get("historical_research_only") is not True
        or data.get("source_claims_verified") is not False
    ):
        raise SystemExit("STOP: Unexpected safety flags")

    reviews = {
        name: validate_review(json.dumps(data[name]))
        for name in ("openai", "claude")
    }

    verdicts = {
        name: review["verdict"]
        for name, review in reviews.items()
    }

    agreement = (
        verdicts["openai"] == verdicts["claude"]
    )

    if agreement != data.get("verdicts_agree"):
        raise SystemExit("STOP: Verdict mismatch")

    if "REJECT" in verdicts.values():
        status = "RESEARCH_REJECTED"
    elif "INSUFFICIENT_DATA" in verdicts.values():
        status = "INSUFFICIENT_EVIDENCE"
    else:
        status = "FURTHER_RESEARCH_ONLY"

    report = {
        "generated_utc":
            datetime.now(timezone.utc).isoformat(),
        "research_topic":
            "Archived SPY/QQQ Discord intelligence",
        "status": status,
        "verdicts_agree": agreement,
        "semantic_agreement_verified": False,
        "model_verdicts": verdicts,
        "independent_theses": {
            name: review["thesis"]
            for name, review in reviews.items()
        },
        "combined_risk_flags":
            attributed_items(reviews, "risk_flags"),
        "combined_missing_data":
            attributed_items(reviews, "missing_data"),
        "data_is_current": False,
        "source_claims_verified": False,
        "research_only": True,
        "execution_enabled": False,
        "trade_authorization": False
    }

    os.umask(0o077)
    OUTPUT.write_text(
        json.dumps(report, indent=2) + "\n"
    )
    OUTPUT.chmod(0o600)

    print("\n=== JAHVASCRIPT COUNCIL SYNTHESIS ===")
    print("OpenAI:", verdicts["openai"])
    print("Claude:", verdicts["claude"])
    print("Verdicts agree:", agreement)
    print("Council status:", status)

    print("\nINDEPENDENT RESEARCH THESES")
    for name, thesis in report["independent_theses"].items():
        print(name.upper() + ":", thesis)

    print("\nRISK FLAGS")
    for item in report["combined_risk_flags"]:
        print(
            "[" + item["source_model"].upper() + "]",
            item["statement"]
        )

    print("\nMISSING EVIDENCE")
    for item in report["combined_missing_data"]:
        print(
            "[" + item["source_model"].upper() + "]",
            item["statement"]
        )

    assert report["trade_authorization"] is False
    assert report["execution_enabled"] is False

    print("\nSaved: council_synthesis_latest.json")
    print("Semantic agreement: NOT AUTOMATICALLY VERIFIED")
    print("Data: ARCHIVED / UNVERIFIED")
    print("\nCOUNCIL SYNTHESIS PASSED")
    print("AI REQUESTS: NONE")
    print("ORDER EXECUTION: DISABLED")


if __name__ == "__main__":
    main()
