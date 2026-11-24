"""JahvaScript research evidence requirements.
Deterministic, offline, and read-only to source data.
No AI calls, brokerage calls, or trade execution.
"""

import json
import os
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent
SOURCE = ROOT / "journal/council_synthesis_latest.json"
OUTPUT = ROOT / "journal/research_evidence_latest.json"

REQUIREMENTS = [
    {
        "id": "EXCHANGE_PRICES",
        "name": "Verified exchange prices and timestamps",
        "evidence_needed": (
            "Independent SPY/QQQ prices, quote timestamps, "
            "data delay, and trading-session information"
        ),
        "keywords": ("price", "timestamp", "exchange")
    },
    {
        "id": "OPTIONS_FLOW",
        "name": "Options-flow provenance and methodology",
        "evidence_needed": (
            "Original data provider, measurement window, "
            "put/call definitions, premium calculation, "
            "and contract-level context"
        ),
        "keywords": (
            "options", "flow", "premium", "volume",
            "current-session", "figures"
        )
    },
    {
        "id": "NEWS_EVENT",
        "name": "Original news event verification",
        "evidence_needed": (
            "Original publication, event timestamp, "
            "source attribution, and independent corroboration"
        ),
        "keywords": (
            "news", "event", "publication", "source",
            "corroborat"
        )
    },
    {
        "id": "MARKET_CONTEXT",
        "name": "Validated intraday market context",
        "evidence_needed": (
            "Correctly timestamped intraday candles, "
            "session boundaries, market structure, "
            "and independently calculated indicators"
        ),
        "keywords": ()
    }
]


def main():
    if not SOURCE.is_file():
        raise SystemExit("STOP: Council synthesis missing")

    gitignore = ROOT / ".gitignore"
    if (
        not gitignore.is_file()
        or "journal/" not in gitignore.read_text().splitlines()
    ):
        raise SystemExit("STOP: Journal must be gitignored")

    synthesis = json.loads(SOURCE.read_text())

    if (
        synthesis.get("status") != "INSUFFICIENT_EVIDENCE"
        or synthesis.get("execution_enabled") is not False
        or synthesis.get("trade_authorization") is not False
        or synthesis.get("data_is_current") is not False
        or synthesis.get("source_claims_verified") is not False
    ):
        raise SystemExit("STOP: Unexpected synthesis state")

    missing = synthesis.get("combined_missing_data", [])

    if not missing:
        raise SystemExit("STOP: Missing evidence list is empty")

    requirements = []

    for template in REQUIREMENTS:
        matches = []

        for entry in missing:
            statement = str(entry.get("statement", ""))
            provider = str(entry.get("source_model", ""))

            if any(
                word in statement.lower()
                for word in template["keywords"]
            ):
                matches.append({
                    "model": provider,
                    "statement": statement
                })

        requirements.append({
            "id": template["id"],
            "name": template["name"],
            "evidence_needed": template["evidence_needed"],
            "status": "NOT_VERIFIED",
            "matched_model_requests": matches,
            "requirement_origin": (
                "COUNCIL_AND_SYSTEM"
                if matches else "SYSTEM_SAFETY_REQUIREMENT"
            ),
            "verification_records": []
        })

    # Historical files may exist, but existence is not
    # proof of current prices or correct intraday bars.
    history = ROOT / "journal/history"

    local_inventory = {
        symbol: {
            "daily_file_present":
                (history / f"{symbol}_D.json").is_file(),
            "satisfies_live_market_verification": False
        }
        for symbol in ("SPY", "QQQ")
    }

    report = {
        "generated_utc": datetime.now(
            timezone.utc
        ).isoformat(),
        "source": "COUNCIL_ARCHIVED_SYNTHESIS",
        "council_status": "INSUFFICIENT_EVIDENCE",
        "requirements": requirements,
        "original_model_missing_data": missing,
        "local_historical_inventory": local_inventory,
        "all_evidence_verified": False,
        "research_only": True,
        "execution_enabled": False,
        "trade_authorization": False
    }

    os.umask(0o077)
    OUTPUT.parent.mkdir(exist_ok=True)
    OUTPUT.write_text(
        json.dumps(report, indent=2) + "\n"
    )
    OUTPUT.chmod(0o600)

    print("\n=== JAHVASCRIPT EVIDENCE ENGINE ===")
    print("Council:", report["council_status"])
    print("Model evidence statements:", len(missing))

    print("\nEVIDENCE REQUIREMENTS")
    for item in requirements:
        print("\n" + item["id"])
        print("Status:", item["status"])
        print(
            "Model evidence matches:",
            len(item["matched_model_requests"])
        )
        print("Required:", item["evidence_needed"])

    print("\nLOCAL HISTORICAL DATA")
    for symbol, info in local_inventory.items():
        print(
            symbol,
            "daily file:",
            "FOUND" if info["daily_file_present"]
            else "NOT FOUND"
        )
        print("Live verification: NOT SATISFIED")

    assert all(
        item["status"] == "NOT_VERIFIED"
        for item in requirements
    )
    assert report["trade_authorization"] is False
    assert report["execution_enabled"] is False

    print("\nSaved: research_evidence_latest.json")
    print("Evidence verification: PENDING")
    print("\nEVIDENCE ENGINE PASSED")
    print("AI REQUESTS: NONE")
    print("ORDER EXECUTION: DISABLED")


if __name__ == "__main__":
    main()
