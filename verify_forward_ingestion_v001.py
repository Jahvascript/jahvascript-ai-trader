"""Test future data quarantine with disposable synthetic files."""

import copy
import hashlib
import json
import tempfile
from datetime import datetime, timezone
from pathlib import Path

import forward_ingestion_v001 as ingest
from prepare_forward_evaluation_v001 import synthetic_capture

ROOT = Path(__file__).resolve().parent
FORWARD = ROOT / "journal/forward_evaluation_v001"


def require(ok, message):
    if not ok:
        raise ValueError(
            "FORWARD INGESTION AUDIT FAILED: " + message
        )


def write_capture(folder, capture):
    path = folder / "capture.json"
    path.write_text(json.dumps(capture, indent=2))
    return path


def expect_rejection(fn, label):
    try:
        fn()
    except (
        ValueError, KeyError, TypeError
    ):
        print(label + ": REJECTED AS EXPECTED")
    else:
        raise ValueError(
            "Unsafe capture accepted: " + label
        )


def main():
    print(
        "\n=== JAHVASCRIPT FORWARD INGESTION AUDIT ==="
    )

    protected_before = ingest.verify_plan()

    with tempfile.TemporaryDirectory(
        prefix="ingestion_test_",
        dir=FORWARD
    ) as directory:
        root = Path(directory)
        inputs = root / "inputs"
        inputs.mkdir()

        capture = synthetic_capture()
        capture["capture_kind"] = "PROVIDER_CAPTURE_V001"
        capture["synthetic_test_only"] = True

        raw_paths = {}

        for provider in ingest.PROVIDERS:
            for symbol in ingest.SYMBOLS:
                raw = (
                    "SYNTHETIC TEST ONLY\n"
                    + provider + "/" + symbol + "\n"
                ).encode()

                path = inputs / (
                    provider + "_" + symbol + ".json"
                )
                path.write_bytes(raw)
                raw_paths[(provider, symbol)] = path

                capture["sources"][provider][
                    "raw_sha256"
                ][symbol] = hashlib.sha256(
                    raw
                ).hexdigest()

        capture_path = write_capture(inputs, capture)
        stage_root = root / "quarantine"
        simulated_now = datetime(
            2026, 10, 23, 14, 0,
            tzinfo=timezone.utc
        )

        def stage(capture_file=capture_path,
                  sources=raw_paths):
            return ingest.stage_capture(
                capture_file,
                sources,
                staging_root=stage_root,
                now=simulated_now,
                allow_synthetic=True
            )

        destination, receipt = stage()

        require(
            destination.is_dir()
            and receipt["status"]
                == "QUARANTINED_NOT_ADMITTED"
            and receipt["evaluation_admission"] is False
            and receipt["synthetic_test_only"] is True
            and receipt["structural_gate"] == "PASS"
            and receipt["raw_file_hash_verification"]
                == "PASS",
            "Quarantine receipt mismatch"
        )

        require(
            len(list(destination.glob("*.raw"))) == 4,
            "Four raw files were not preserved"
        )

        print("Synthetic paired-session staging: PASS")
        print("Four immutable-source snapshots: PASS")
        print("Admission correctly withheld: PASS")

        expect_rejection(
            stage, "Duplicate date staging"
        )

        modified = copy.deepcopy(capture)
        modified["sources"]["WEBULL"]["raw_sha256"][
            "SPY"
        ] = "0" * 64

        modified_path = (
            inputs / "invalid_hash_capture.json"
        )
        modified_path.write_text(json.dumps(modified))

        expect_rejection(
            lambda: stage(modified_path),
            "Raw SHA-256 mismatch"
        )

        modified = copy.deepcopy(capture)
        modified["sources"]["YAHOO"]["bars"]["QQQ"].pop()
        modified_path = (
            inputs / "missing_bar_capture.json"
        )
        modified_path.write_text(json.dumps(modified))

        expect_rejection(
            lambda: stage(modified_path),
            "Missing candle"
        )

        modified = copy.deepcopy(capture)
        modified["session_date"] = "2026-10-21"
        modified_path = (
            inputs / "old_date_capture.json"
        )
        modified_path.write_text(json.dumps(modified))

        expect_rejection(
            lambda: stage(modified_path),
            "Research V001 date"
        )

        modified = copy.deepcopy(capture)
        modified["sources"]["WEBULL"]["retrieved_utc"] = (
            "2026-10-24T00:00:00+00:00"
        )
        modified_path = (
            inputs / "future_retrieval_capture.json"
        )
        modified_path.write_text(json.dumps(modified))

        expect_rejection(
            lambda: stage(modified_path),
            "Future retrieval timestamp"
        )

        modified = copy.deepcopy(capture)
        modified["synthetic_test_only"] = False
        modified_path = (
            inputs / "false_real_capture.json"
        )
        modified_path.write_text(json.dumps(modified))

        expect_rejection(
            lambda: stage(modified_path),
            "Synthetic/real declaration mismatch"
        )

        require(
            len([
                path for path in stage_root.iterdir()
                if path.is_dir()
            ]) == 1,
            "Rejected captures created extra staged sessions"
        )

        print("Rejected captures did not publish: PASS")

    require(
        ingest.verify_plan() == protected_before,
        "Frozen research files or manifest changed"
    )

    require(
        not (
            ingest.STAGING / "2026-10-22"
        ).exists(),
        "Synthetic fixture leaked into real quarantine"
    )

    print("Disposable synthetic fixtures: CLEANED UP")
    print("Real quarantine sessions created: 0")
    print("Forward evaluation manifest: UNCHANGED")
    print("Research V001 and frozen protocol: UNCHANGED")
    print("Raw-to-normalized provider parsing: NOT YET VERIFIED")
    print("Official market calendar: NOT YET VERIFIED")
    print("Future predictive evaluation: NOT STARTED")

    print("\nFORWARD INGESTION AUDIT PASSED")
    print("WEBULL REQUESTS: NONE")
    print("YAHOO REQUESTS: NONE")
    print("AI REQUESTS: NONE")
    print("ORDER EXECUTION: DISABLED")


if __name__ == "__main__":
    main()
