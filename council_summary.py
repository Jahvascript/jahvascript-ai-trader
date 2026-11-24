"""Read existing Council results without calling paid APIs."""

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent
JOURNAL = ROOT / "journal"


def main():
    reports = sorted(
        JOURNAL.glob("webull_ai_research_*.json")
    )

    print("\n=== JAHVASCRIPT COUNCIL SUMMARY ===")
    print("Mode: LOCAL JOURNAL REVIEW")
    print("AI requests: NONE")
    print("Broker orders: DISABLED")

    if not reports:
        print("No market research journal found")
        return

    latest = reports[-1]
    data = json.loads(latest.read_text())

    if data.get("source") != "WEBULL_SANDBOX_DELAYED":
        raise ValueError("Unexpected research source")

    print("Journal:", latest.name)

    for item in data.get("results", []):
        candidate = item["candidate"]
        providers = item.get("providers", {})

        print("\n---", candidate["underlying"], "---")
        print("Contract:", candidate["option_symbol"])
        print(
            "Quote age at review:",
            candidate.get("quote_age_minutes_now"),
            "minutes"
        )

        verdicts = []
        successful = True

        for name in ("openai", "claude"):
            result = providers.get(name, {})

            if result.get("status") != "OK":
                successful = False
                print(name.upper(), "INCOMPLETE")
                continue

            review = result["review"]
            verdicts.append(review["verdict"])

            print(
                name.upper(),
                "VERDICT:",
                review["verdict"]
            )

            missing = review.get("missing_data", [])

            for issue in missing[:4]:
                print("  Missing:", issue)

        if not successful or len(verdicts) != 2:
            decision = "INCOMPLETE"
        elif any(
            v in ("REJECT", "INSUFFICIENT_DATA")
            for v in verdicts
        ):
            decision = "NON_ACTIONABLE"
        elif verdicts == ["RESEARCH", "RESEARCH"]:
            decision = "RESEARCH_ONLY"
        else:
            decision = "UNRESOLVED"

        print("Council classification:", decision)
        print("Trading authorization: NONE")

    print("\nSUMMARY COMPLETE")
    print("NO NEW API CHARGES")
    print("ORDER EXECUTION: DISABLED")


if __name__ == "__main__":
    main()
