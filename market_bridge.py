"""Webull sandbox -> local research candidates.
No AI calls. No brokerage orders.
"""

import json
import logging
import os
from datetime import datetime, timezone
from pathlib import Path

logging.disable(logging.CRITICAL)
os.umask(0o077)

from webull.core.client import ApiClient
from webull.trade.trade_client import TradeClient
from webull.data.data_client import DataClient

from options_scanner import (
    load_config, records,
    select_samples, next_expiration
)
from sandbox_research import assess_quote


ROOT = Path(__file__).resolve().parent


def get_json(response, label):
    if response.status_code != 200:
        raise RuntimeError(
            f"{label}: HTTP {response.status_code}"
        )
    return response.json()


def main():
    config = load_config()

    if config.get("WEBULL_ENVIRONMENT") != "uat":
        raise RuntimeError("Sandbox required")

    api = ApiClient(
        config["WEBULL_APP_KEY"],
        config["WEBULL_APP_SECRET"],
        "us"
    )
    api.add_endpoint("us", "api.sandbox.webull.com")

    TradeClient(api)
    data = DataClient(api)

    expiry = next_expiration()

    report = {
        "source": "WEBULL_SANDBOX_DELAYED",
        "created_utc": datetime.now(
            timezone.utc
        ).isoformat(),
        "expiration": expiry,
        "execution_enabled": False,
        "live_quote_verified": False,
        "ai_requests": 0,
        "candidates": [],
        "warnings": []
    }

    print("\n=== JAHVASCRIPT MARKET BRIDGE ===")
    print("Source: WEBULL SANDBOX")
    print("Data: DELAYED / RESEARCH ONLY")
    print("Expiration:", expiry)

    for ticker in ("SPY", "QQQ"):
        print(f"\n--- {ticker} ---")

        try:
            payload = get_json(
                data.instrument.list_option_contracts(
                    category="US_OPTION",
                    underlying_symbols=ticker,
                    status="LISTING",
                    start_date=expiry,
                    end_date=expiry
                ),
                "Contracts"
            )

            if (
                isinstance(payload, dict)
                and payload.get("pagination_key")
            ):
                print("SKIP: Incomplete contract pages")
                report["warnings"].append(
                    f"{ticker}: Pagination not completed"
                )
                continue

            contracts = records(payload)

            if not isinstance(contracts, list):
                print("SKIP: Unknown contract data")
                continue

            samples = select_samples(
                contracts, ticker, expiry
            )

            if not samples:
                print("SKIP: No matching contracts")
                continue

            symbols = list(dict.fromkeys(
                c["symbol"] for c in samples
            ))[:8]

            quotes_payload = get_json(
                data.option_market_data.get_option_snapshot(
                    symbols=",".join(symbols),
                    category="US_OPTION"
                ),
                "Snapshots"
            )

            quotes = records(quotes_payload)

            if not isinstance(quotes, list):
                print("SKIP: Unknown quote data")
                continue

            lookup = {
                q["symbol"]: q
                for q in quotes
                if isinstance(q, dict)
                and isinstance(q.get("symbol"), str)
            }

            eligible = []

            for contract in samples:
                symbol = contract["symbol"]
                quote = lookup.get(symbol)

                if quote is None:
                    continue

                check = assess_quote(quote)

                if not check["research_eligible"]:
                    print(
                        "Research screen BLOCKED:",
                        symbol
                    )
                    continue

                candidate = {
                    "source": "WEBULL_SANDBOX_DELAYED",
                    "underlying": ticker,
                    "option_symbol": symbol,
                    "option_type": contract["option_type"],
                    "expiration": expiry,
                    "strike": str(
                        contract["strike_price"]
                    ),
                    "multiplier": str(
                        contract["multiplier"]
                    ),
                    "bid": str(quote["bid"]),
                    "ask": str(quote["ask"]),
                    "volume": str(
                        quote.get("volume", "")
                    ),
                    "open_interest": str(
                        quote.get("open_interest", "")
                    ),
                    "delta": str(
                        quote.get("delta", "")
                    ),
                    "implied_volatility": str(
                        quote.get("imp_vol", "")
                    ),
                    "quote_time": str(
                        quote.get("quote_time", "")
                    ),
                    "quote_age_minutes": round(
                        check["age_minutes"], 2
                    ),
                    "spread_pct": round(
                        float(check["spread_pct"]), 3
                    ),
                    "execution_enabled": False,
                    "research_only": True
                }

                eligible.append(candidate)

            if not eligible:
                print("No research-quality samples")
                continue

            # Diagnostic sample only, not a trade ranking
            eligible.sort(
                key=lambda c: c["spread_pct"]
            )

            chosen = eligible[0]
            report["candidates"].append(chosen)

            print("Research sample:", chosen["option_symbol"])
            print("Bid:", chosen["bid"])
            print("Ask:", chosen["ask"])
            print("Spread:", chosen["spread_pct"], "%")
            print(
                "Quote age:",
                chosen["quote_age_minutes"],
                "minutes"
            )
            print("Status: RESEARCH ONLY")

        except Exception as exc:
            print(
                "Request stopped:",
                type(exc).__name__
            )
            report["warnings"].append(
                f"{ticker}: Request could not complete"
            )

    journal = ROOT / "journal"

    if "journal/" not in (
        ROOT / ".gitignore"
    ).read_text().splitlines():
        raise RuntimeError("Journal must be gitignored")

    journal.mkdir(mode=0o700, exist_ok=True)

    output = journal / "webull_candidates_latest.json"

    output.write_text(
        json.dumps(report, indent=2) + "\n"
    )
    output.chmod(0o600)

    print("\nCandidates saved:",
          len(report["candidates"]))
    print("Journal: journal/webull_candidates_latest.json")
    print("AI REQUESTS: NONE")
    print("ORDER EXECUTION: DISABLED")
    print("MARKET BRIDGE COMPLETE")


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        print(
            "BRIDGE STOPPED:",
            type(exc).__name__
        )
