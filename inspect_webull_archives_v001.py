"""Inspect archived Webull JSON structure safely.

Prints only structural metadata:
field names, data types, collection sizes, file hashes.

Never prints JSON field values, credentials, or prices.
Never modifies historical research files.
"""

import hashlib
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent
JOURNAL = ROOT / "journal"

DATASET = JOURNAL / "datasets/M5_SPY_QQQ_v001.json"

EXPECTED_DATASET_SHA256 = (
    "4c55e6c7961e9210f73f1468b20498fe20db4941cd27b10bd11753336292e3cb"
)

ARCHIVES = (
    "history/SPY_M5.json",
    "history/QQQ_M5.json",
    "history/backfill/SPY_M5_backfill_001.json",
    "history/backfill/QQQ_M5_backfill_001.json",
    "history/backfill/batch_002/SPY_M5.json",
    "history/backfill/batch_002/QQQ_M5.json",
    "history/backfill/batch_003/SPY_M5.json",
    "history/backfill/batch_003/QQQ_M5.json",
)


def require(condition, message):
    if not condition:
        raise ValueError(
            "WEBULL INSPECTION STOPPED: " + message
        )


def sha256(data):
    return hashlib.sha256(data).hexdigest()


def typename(value):
    if isinstance(value, dict):
        return "object"
    if isinstance(value, list):
        return "array"
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "boolean"
    if isinstance(value, int):
        return "integer"
    if isinstance(value, float):
        return "number"
    if isinstance(value, str):
        return "string"
    return "unknown"


def structure(value, location="root", depth=0):
    """Display structure, never primitive contents."""

    indent = "  " * depth

    if isinstance(value, dict):
        keys = list(value)

        print(
            indent + location,
            "| object | fields:",
            len(keys)
        )

        # Print field names only.
        print(
            indent + "  keys:",
            ", ".join(str(key) for key in keys[:30])
        )

        if len(keys) > 30:
            print(
                indent + "  additional fields:",
                len(keys) - 30
            )

        if depth >= 3:
            return

        # Explore nested containers only.
        for key, child in list(value.items())[:30]:
            if isinstance(child, (dict, list)):
                structure(
                    child,
                    str(key),
                    depth + 1
                )

    elif isinstance(value, list):
        print(
            indent + location,
            "| array | elements:",
            len(value)
        )

        if value and depth < 4:
            first = value[0]

            print(
                indent + "  element type:",
                typename(first)
            )

            if isinstance(first, (dict, list)):
                structure(
                    first,
                    "first_element",
                    depth + 1
                )

    else:
        # Primitive field values are deliberately hidden.
        print(
            indent + location,
            "|",
            typename(value)
        )


def main():
    print(
        "\n=== JAHVASCRIPT WEBULL ARCHIVE INSPECTION ==="
    )

    require(
        "journal/" in (
            ROOT / ".gitignore"
        ).read_text().splitlines(),
        "Research journal is not gitignored"
    )

    before = DATASET.read_bytes()

    require(
        sha256(before) == EXPECTED_DATASET_SHA256,
        "Frozen dataset fingerprint mismatch"
    )

    files_checked = 0

    for filename in ARCHIVES:
        path = JOURNAL / filename

        print("\n" + "=" * 55)
        print("ARCHIVE:", filename)
        print("=" * 55)

        require(
            path.is_file() and not path.is_symlink(),
            "Missing or linked archive: " + filename
        )

        raw = path.read_bytes()

        require(
            0 < len(raw) <= 20_000_000,
            "Unexpected archive size: " + filename
        )

        original_hash = sha256(raw)

        print("File size:", len(raw), "bytes")
        print("SHA-256:", original_hash)
        print("JSON STRUCTURE:")

        try:
            payload = json.loads(raw)
        except json.JSONDecodeError as error:
            raise ValueError(
                "Invalid JSON in " + filename
            ) from error

        structure(payload)

        require(
            sha256(path.read_bytes()) == original_hash,
            "Archive modified during inspection"
        )

        print("Read-only integrity: PASS")
        files_checked += 1

    require(
        sha256(DATASET.read_bytes())
        == EXPECTED_DATASET_SHA256,
        "Frozen dataset modified"
    )

    print("\n" + "=" * 55)
    print("WEBULL ARCHIVE STRUCTURE INSPECTION COMPLETE")
    print("Archives inspected:", files_checked)
    print("Frozen Dataset V001: UNCHANGED")
    print("Archived source files: UNCHANGED")
    print("Raw Webull parser verified: NOT YET")
    print("Real future sessions admitted: 0")
    print("WEBULL REQUESTS: NONE")
    print("YAHOO REQUESTS: NONE")
    print("AI REQUESTS: NONE")
    print("ORDER EXECUTION: DISABLED")
    print("=" * 55)


if __name__ == "__main__":
    main()
