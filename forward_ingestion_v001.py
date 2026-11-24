"""JahvaScript V001 future market-data quarantine.

Stage provider files without admitting them to the holdout.
Structural checks cannot certify vendor candle extraction.
No network requests, AI calls, signals, or orders.
"""

import argparse
import fcntl
import hashlib
import json
import os
import shutil
import sys
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

from prepare_forward_evaluation_v001 import validate_capture

ROOT = Path(__file__).resolve().parent
JOURNAL = ROOT / "journal"
FORWARD = JOURNAL / "forward_evaluation_v001"
MANIFEST = FORWARD / "evaluation_manifest_v001.json"
FROZEN = (
    JOURNAL / "price_verification"
    / "frozen_research_protocol_v001.json"
)
DATASET = JOURNAL / "datasets/M5_SPY_QQQ_v001.json"
STAGING = FORWARD / "quarantine"

EXPECTED_DATASET_HASH = (
    "4c55e6c7961e9210f73f1468b20498fe20db4941cd27b10bd11753336292e3cb"
)
MAX_SOURCE_BYTES = 16_000_000
PROVIDERS = ("WEBULL", "YAHOO")
SYMBOLS = ("SPY", "QQQ")


def require(ok, reason):
    if not ok:
        raise ValueError("INGESTION STOPPED: " + reason)


def digest(data):
    return hashlib.sha256(data).hexdigest()


def read_regular(path):
    path = Path(path)
    require(
        path.is_file() and not path.is_symlink(),
        "Missing, nonregular, or linked input: " + str(path)
    )
    require(
        path.stat().st_size <= MAX_SOURCE_BYTES,
        "Input exceeds safety size limit: " + str(path)
    )
    return path.read_bytes()


def verify_plan():
    dataset_raw = read_regular(DATASET)
    protocol_raw = read_regular(FROZEN)
    manifest_raw = read_regular(MANIFEST)

    require(
        digest(dataset_raw) == EXPECTED_DATASET_HASH,
        "Original Dataset V001 changed"
    )

    protocol = json.loads(protocol_raw)
    plan = json.loads(manifest_raw)

    require(
        protocol.get("version")
            == "FROZEN_RESEARCH_PROTOCOL_V001"
        and protocol.get("exploratory_dataset_sha256")
            == EXPECTED_DATASET_HASH
        and protocol.get("execution_enabled") is False
        and protocol.get("trade_authorization") is False,
        "Frozen protocol mismatch"
    )

    require(
        plan.get("version")
            == "FORWARD_EVALUATION_PLAN_V001"
        and plan.get("frozen_protocol_sha256")
            == digest(protocol_raw)
        and plan.get("exploratory_dataset_sha256")
            == EXPECTED_DATASET_HASH
        and plan.get("eligible_date_strictly_after")
            == "2026-10-21"
        and plan.get("target_complete_sessions") == 20
        and plan.get("accepted_real_sessions") == 0
        and plan.get("evaluation_started") is False
        and plan.get("new_data_received") is False
        and plan.get("status")
            == "PREPARED_AWAITING_NEW_SESSIONS"
        and plan.get("execution_enabled") is False
        and plan.get("trade_authorization") is False,
        "Forward evaluation manifest mismatch"
    )

    return {
        "dataset": digest(dataset_raw),
        "protocol": digest(protocol_raw),
        "manifest": digest(manifest_raw)
    }


def private_write(path, content):
    fd = os.open(
        str(path),
        os.O_WRONLY | os.O_CREAT | os.O_EXCL,
        0o600
    )
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
    except BaseException:
        raise


def stage_capture(
    capture_path,
    raw_paths,
    staging_root=STAGING,
    *,
    now=None,
    allow_synthetic=False,
    verify_raw_mapping=False
):
    """Quarantine one paired session without evaluating it.

    raw_paths uses (PROVIDER, SYMBOL) tuple keys.
    A collector/parser must later independently prove that
    normalized bars actually came from these raw responses.
    """
    before = verify_plan()

    require(
        set(raw_paths) == {
            (provider, symbol)
            for provider in PROVIDERS
            for symbol in SYMBOLS
        },
        "Exactly four provider/symbol raw files required"
    )

    capture_bytes = read_regular(capture_path)
    capture = json.loads(capture_bytes)

    require(
        capture.get("capture_kind")
            == "PROVIDER_CAPTURE_V001",
        "Unrecognized normalized capture format"
    )

    synthetic = capture.get("synthetic_test_only")
    require(
        synthetic is True if allow_synthetic
        else synthetic is False,
        "Synthetic/real capture declaration mismatch"
    )

    gate = validate_capture(capture)
    day = gate["session_date"]

    current = now or datetime.now(timezone.utc)
    require(
        current.tzinfo is not None,
        "Current time must have a timezone"
    )
    current = current.astimezone(timezone.utc)

    require(
        current.date() >= datetime.fromisoformat(day).date(),
        "Session date lies in the future"
    )

    raw_content = {}
    raw_receipts = {}

    for provider in PROVIDERS:
        retrieved = datetime.fromisoformat(
            capture["sources"][provider]["retrieved_utc"]
        ).astimezone(timezone.utc)

        require(
            retrieved <= current + timedelta(minutes=5),
            "Provider retrieval timestamp is in the future"
        )

        for symbol in SYMBOLS:
            key = (provider, symbol)
            content = read_regular(raw_paths[key])
            actual_hash = digest(content)
            declared_hash = (
                capture["sources"][provider]["raw_sha256"][
                    symbol
                ]
            )

            require(
                actual_hash == declared_hash,
                provider + " " + symbol
                + ": raw-source hash mismatch"
            )

            raw_content[key] = content
            raw_receipts[provider + "_" + symbol] = {
                "sha256": actual_hash,
                "bytes": len(content)
            }

    mapping_result = None
    if verify_raw_mapping:
        from dual_provider_mapping_v001 import verify_mapping
        mapping_result = verify_mapping(
            capture, raw_content,
            synthetic=allow_synthetic
        )
        require(
            mapping_result["raw_to_normalized_mapping"] == "PASS"
            and mapping_result["verified_candles"] == 312,
            "Raw-to-normalized mapping is incomplete"
        )

    require(
        verify_plan() == before,
        "Protected research plan changed during validation"
    )

    staging_root = Path(staging_root)
    staging_root.mkdir(
        parents=True, exist_ok=True, mode=0o700
    )
    require(
        not staging_root.is_symlink(),
        "Quarantine root cannot be a symlink"
    )

    lock_path = staging_root / ".ingest.lock"
    fd = os.open(
        str(lock_path),
        os.O_CREAT | os.O_RDWR,
        0o600
    )

    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)

        destination = staging_root / day
        require(
            not destination.exists()
            and not destination.is_symlink(),
            "This session date is already staged"
        )

        temp = Path(tempfile.mkdtemp(
            prefix=".intake_",
            dir=staging_root
        ))
        os.chmod(temp, 0o700)

        try:
            for provider in PROVIDERS:
                for symbol in SYMBOLS:
                    filename = (
                        provider + "_" + symbol + ".raw"
                    )
                    private_write(
                        temp / filename,
                        raw_content[(provider, symbol)]
                    )

            private_write(
                temp / "normalized_capture.json",
                capture_bytes
            )

            receipt = {
                "version": "QUARANTINE_RECEIPT_V001",
                "session_date": day,
                "staged_at_utc": current.isoformat(),
                "synthetic_test_only": synthetic,
                "normalized_capture_sha256":
                    digest(capture_bytes),
                "raw_sources": raw_receipts,
                "protected_plan_fingerprints": before,
                "structural_gate": "PASS",
                "raw_file_hash_verification": "PASS",
                "raw_to_normalized_extraction":
                    "PARSED_AND_MATCHED_SUPPORTED_FORMAT"
                    if mapping_result is not None
                    else "NOT_INDEPENDENTLY_VERIFIED",
                "raw_to_normalized_candles_verified":
                    mapping_result["verified_candles"]
                    if mapping_result is not None else 0,
                "verified_webull_format_scope":
                    mapping_result["supported_webull_format"]
                    if mapping_result is not None else "NONE",
                "official_exchange_calendar":
                    "NOT_VERIFIED",
                "source_prices": "NOT_CERTIFIED",
                "evaluation_admission": False,
                "status": "QUARANTINED_NOT_ADMITTED",
                "trade_signal": False,
                "execution_enabled": False,
                "trade_authorization": False
            }

            private_write(
                temp / "receipt.json",
                (
                    json.dumps(receipt, indent=2) + "\n"
                ).encode()
            )

            for name, details in raw_receipts.items():
                require(
                    digest((temp / (name + ".raw")).read_bytes())
                    == details["sha256"],
                    "Saved raw source changed"
                )

            require(
                digest(
                    (temp / "normalized_capture.json").read_bytes()
                ) == digest(capture_bytes),
                "Saved normalized capture changed"
            )

            require(
                verify_plan() == before,
                "Protected plan changed before publication"
            )

            os.rename(temp, destination)

        finally:
            if temp.exists():
                shutil.rmtree(temp)

    finally:
        os.close(fd)

    return destination, receipt


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--capture", type=Path, required=True)
    parser.add_argument("--webull-spy", type=Path, required=True)
    parser.add_argument("--webull-qqq", type=Path, required=True)
    parser.add_argument("--yahoo-spy", type=Path, required=True)
    parser.add_argument("--yahoo-qqq", type=Path, required=True)
    args = parser.parse_args()

    paths = {
        ("WEBULL", "SPY"): args.webull_spy,
        ("WEBULL", "QQQ"): args.webull_qqq,
        ("YAHOO", "SPY"): args.yahoo_spy,
        ("YAHOO", "QQQ"): args.yahoo_qqq
    }

    location, receipt = stage_capture(
        args.capture, paths
    )

    print("Staging directory:", location)
    print("Raw-source fingerprints: VERIFIED")
    print("78-bar structural session gate: PASSED")
    print(
        "Provider extraction mapping:",
        "NOT INDEPENDENTLY VERIFIED"
    )
    print("Holdout admission: NO")
    print("Status:", receipt["status"])
    print("ORDER EXECUTION: DISABLED")


if __name__ == "__main__":
    try:
        main()
    except (
        ValueError, KeyError, TypeError,
        OSError, json.JSONDecodeError
    ) as error:
        print(str(error), file=sys.stderr)
        sys.exit(1)
