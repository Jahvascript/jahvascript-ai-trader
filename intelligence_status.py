import json
from pathlib import Path

path = Path(__file__).resolve().parent
config = json.loads(
    (path / "intelligence_sources.json").read_text()
)

assert config["execution_enabled"] is False

print("\n=== JAHVASCRIPT MARKET INTELLIGENCE ===")
print("STEP 15B: SOURCE REGISTRY\n")

for source_id, spec in config["sources"].items():
    assert spec["direct_entry_signal"] is False
    assert spec["connection"] in (
        "not_connected", "connected"
    )

    print(spec["name"])
    print("  Role:", spec["role"])
    print("  Connection:", spec["connection"])
    print("  Entry authority: NONE\n")

print("SOURCE REGISTRY COMPLETE")
print("LIVE FEEDS: NOT CONNECTED")
print("AI REQUESTS: NONE")
print("ORDER EXECUTION: DISABLED")
