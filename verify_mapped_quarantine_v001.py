"""Verify parser-gated quarantine with disposable fixtures."""

import copy
import hashlib
import json
import os
import tempfile
from datetime import datetime, timezone
from pathlib import Path

import forward_ingestion_v001 as ingestion
from prepare_forward_evaluation_v001 import synthetic_capture
from verify_dual_provider_mapping_v001 import synthetic_raw_sources

ROOT = Path(__file__).resolve().parent
FORWARD = ROOT / "journal/forward_evaluation_v001"
OUTPUT = FORWARD / "mapped_quarantine_audit_v001.json"

NOW = datetime(
    2026, 10, 23, 14, 0,
    tzinfo=timezone.utc
)


def require(ok, message):
    if not ok:
        raise ValueError(
            "MAPPED QUARANTINE AUDIT STOPPED: " + message
        )


def save_inputs(folder, capture, raw):
    folder.mkdir(parents=True, exist_ok=True)

    capture_path = folder / "normalized_capture.json"
    capture_path.write_text(json.dumps(capture, indent=2))

    paths = {}

    for provider in ("WEBULL", "YAHOO"):
        for symbol in ("SPY", "QQQ"):
            key = (provider, symbol)
            path = folder / (provider + "_" + symbol + ".raw")
            path.write_bytes(raw[key])
            paths[key] = path

    return capture_path, paths


def attempt(capture_path, paths, root, verified=True):
    return ingestion.stage_capture(
        capture_path,
        paths,
        staging_root=root,
        now=NOW,
        allow_synthetic=True,
        verify_raw_mapping=verified
    )


def reject(capture, raw, folder, label):
    path, paths = save_inputs(
        folder / "input", capture, raw
    )
    destination = folder / "quarantine"

    try:
        attempt(path, paths, destination, verified=True)
    except (
        ValueError,
        KeyError,
        TypeError
    ):
        require(
            not (destination / "2026-10-22").exists(),
            label + ": invalid session was published"
        )
        print(label + ": REJECTED AS EXPECTED")
    else:
        raise ValueError(
            "Invalid verified quarantine accepted: " + label
        )


def main():
    print(
        "\n=== JAHVASCRIPT MAPPED QUARANTINE AUDIT ==="
    )

    require(
        not OUTPUT.exists(),
        "V001 mapped quarantine audit already exists"
    )

    before = ingestion.verify_plan()

    capture = synthetic_capture()
    capture["capture_kind"] = "PROVIDER_CAPTURE_V001"
    capture["synthetic_test_only"] = True
    raw = synthetic_raw_sources(capture)

    with tempfile.TemporaryDirectory(
        prefix="mapped_quarantine_test_",
        dir=FORWARD
    ) as temp:
        root = Path(temp)

        capture_path, paths = save_inputs(
            root / "valid_inputs", capture, raw
        )

        destination, receipt = attempt(
            capture_path,
            paths,
            root / "verified_quarantine"
        )

        require(
            destination.is_dir()
            and receipt["status"] == "QUARANTINED_NOT_ADMITTED"
            and receipt["raw_to_normalized_extraction"]
                == "PARSED_AND_MATCHED_SUPPORTED_FORMAT"
            and receipt["raw_to_normalized_candles_verified"]
                == 312
            and receipt["verified_webull_format_scope"]
                == "SAVED_RESEARCH_ARCHIVE_ONLY"
            and receipt["evaluation_admission"] is False,
            "Verified quarantine receipt is incorrect"
        )

        saved = json.loads(
            (destination / "receipt.json").read_text()
        )

        require(
            saved == receipt,
            "Published mapping receipt differs"
        )

        for (provider, symbol), original in raw.items():
            saved_raw = (
                destination
                / (provider + "_" + symbol + ".raw")
            ).read_bytes()

            require(
                saved_raw == original
                and hashlib.sha256(saved_raw).hexdigest()
                    == receipt["raw_sources"][
                        provider + "_" + symbol
                    ]["sha256"],
                "Published raw snapshot differs"
            )

        print("Parser-verified quarantine receipt: PASS")
        print("All 312 saved candle mappings: VERIFIED")
        print("Four raw-source snapshot hashes: PASS")
        print("Future evaluation admission: BLOCKED")

        # Previous basic-quarantine behavior remains
        # available, but cannot claim parser verification.
        _, basic = attempt(
            capture_path,
            paths,
            root / "basic_quarantine",
            verified=False
        )

        require(
            basic["raw_to_normalized_extraction"]
                == "NOT_INDEPENDENTLY_VERIFIED"
            and basic["raw_to_normalized_candles_verified"] == 0
            and basic["evaluation_admission"] is False,
            "Basic quarantine incorrectly claims mapping"
        )

        print("Legacy basic quarantine compatibility: PASS")
        print("Unverified receipt cannot claim mapping: PASS")

        # Changing a normalized candle while keeping the
        # raw bytes untouched must fail.
        bad = copy.deepcopy(capture)
        bad["sources"]["WEBULL"]["bars"][
            "SPY"
        ][0]["close"] = "770.01"

        reject(
            bad, raw, root / "changed_normalized",
            "Modified normalized candle"
        )

        # Changing raw bytes but preserving the old hash
        # must fail the fingerprint check.
        altered = dict(raw)
        yahoo = json.loads(raw[("YAHOO", "QQQ")])
        yahoo["chart"]["result"][0]["indicators"][
            "quote"
        ][0]["close"][0] = "700.01"

        altered[("YAHOO", "QQQ")] = json.dumps(
            yahoo
        ).encode()

        reject(
            capture, altered,
            root / "changed_raw",
            "Modified raw response"
        )

        # Even when the attacker updates the declared
        # hash, the raw-to-normalized mapping must fail.
        forged = copy.deepcopy(capture)
        forged["sources"]["YAHOO"]["raw_sha256"][
            "QQQ"
        ] = hashlib.sha256(
            altered[("YAHOO", "QQQ")]
        ).hexdigest()

        reject(
            forged, altered,
            root / "forged_hash",
            "Modified raw response with updated hash"
        )

        wrong = copy.deepcopy(capture)
        wrong["sources"]["WEBULL"]["format"] = (
            "UNVERIFIED_LIVE_HTTP"
        )

        reject(
            wrong, raw,
            root / "unsupported",
            "Unverified Webull HTTP format"
        )

        missing = copy.deepcopy(capture)
        missing["sources"]["YAHOO"]["bars"][
            "SPY"
        ].pop()

        reject(
            missing, raw,
            root / "incomplete",
            "Incomplete normalized session"
        )

        # Verified input can only be staged once at
        # a given destination.
        try:
            attempt(
                capture_path, paths,
                root / "verified_quarantine"
            )
        except ValueError:
            print(
                "Duplicate verified session: REJECTED AS EXPECTED"
            )
        else:
            raise ValueError(
                "Duplicate verified session was accepted"
            )

    require(
        ingestion.verify_plan() == before,
        "Frozen dataset, protocol, or manifest changed"
    )

    require(
        not (ingestion.STAGING / "2026-10-22").exists(),
        "Synthetic session entered the real quarantine"
    )

    report = {
        "version": "MAPPED_QUARANTINE_AUDIT_V001",
        "generated_utc": datetime.now(
            timezone.utc
        ).isoformat(),
        "protected_plan_fingerprints": before,
        "verified_synthetic_candles": 312,
        "rejected_invalid_cases": 6,
        "basic_quarantine_regression": "PASS",
        "verified_receipt": "PASS",
        "unverified_live_webull_format_rejected": True,
        "real_future_sessions_admitted": 0,
        "official_exchange_calendar_verified": False,
        "real_provider_origin_authenticated": False,
        "live_webull_http_parser_verified": False,
        "synthetic_fixture_data_persisted": False,
        "execution_enabled": False,
        "trade_authorization": False
    }

    os.umask(0o077)

    with OUTPUT.open("x") as handle:
        json.dump(report, handle, indent=2)
        handle.write("\n")

    OUTPUT.chmod(0o600)

    require(
        ingestion.verify_plan() == before,
        "Protected plan changed after audit"
    )

    print("\n--- FINAL RESULT ---")
    print("Verified quarantine staging: PASS")
    print("Invalid/duplicate session rejections: 6/6 PASS")
    print("Basic quarantine behavior: PRESERVED")
    print("Temporary synthetic files: CLEANED UP")
    print("Original research and manifest: UNCHANGED")
    print("Real future sessions admitted: 0")
    print("Live Webull HTTP parser: NOT VERIFIED")
    print("Official exchange calendar: NOT VERIFIED")
    print(
        "Saved: journal/forward_evaluation_v001/"
        "mapped_quarantine_audit_v001.json"
    )

    print("\nMAPPED QUARANTINE AUDIT PASSED")
    print("WEBULL REQUESTS: NONE")
    print("YAHOO REQUESTS: NONE")
    print("AI REQUESTS: NONE")
    print("ORDER EXECUTION: DISABLED")


if __name__ == "__main__":
    main()
