"""Verify read-only dashboard integration using disposable fixtures."""

import hashlib
import json
import subprocess
import sys
import tempfile
from pathlib import Path

import replay_checkpoint_v001 as cp

ROOT = Path(__file__).resolve().parent
JOURNAL = ROOT / "journal"
DASHBOARD = ROOT / "research_dashboard_v001.py"

PROTECTED = [
    JOURNAL / "datasets/M5_SPY_QQQ_v001.json",
    JOURNAL / "expanded_research_v001.json",
    JOURNAL / "rvol_clock_normalized_v001.json",
    JOURNAL / "market_state_v001.json",
    JOURNAL / "market_replay_v001.json",
    JOURNAL / "market_replay_timeline_audit_v001.json"
]


def require(ok, message):
    if not ok:
        raise ValueError(
            "DASHBOARD AUDIT FAILED: " + message
        )


def inspect_process(path, expected_code=0):
    result = subprocess.run(
        [
            sys.executable,
            str(DASHBOARD),
            "--checkpoint",
            str(path),
            "--json"
        ],
        capture_output=True,
        text=True,
        timeout=20
    )

    require(
        result.returncode == expected_code,
        "Dashboard subprocess returned "
        + str(result.returncode)
        + ": "
        + result.stdout
        + result.stderr
    )

    report = json.loads(result.stdout)

    require(
        report["dashboard_is_read_only"] is True
        and report["dataset"]["status"] == "VERIFIED"
        and report["research"]["transitions_verified"] == 656
        and report["safety"]["order_execution_enabled"] is False
        and report["safety"]["market_api_requests"] is False
        and report["safety"]["ai_requests"] is False,
        "Dashboard integrity or safety mismatch"
    )

    return report


def main():
    print(
        "\n=== JAHVASCRIPT DASHBOARD INTEGRITY AUDIT ==="
    )

    require(
        "journal/" in (
            ROOT / ".gitignore"
        ).read_text().splitlines(),
        "Journal must be gitignored"
    )

    before = {
        str(path): hashlib.sha256(
            path.read_bytes()
        ).hexdigest()
        for path in PROTECTED
    }

    stream, source_hash = cp.load_verified_stream(
        ROOT
    )

    require(
        len(stream) == 656,
        "Unexpected verified stream length"
    )

    with tempfile.TemporaryDirectory(
        prefix="dashboard_test_",
        dir=JOURNAL
    ) as directory:
        folder = Path(directory)
        path = folder / "session.json"

        missing = inspect_process(path)

        require(
            missing["session"]["status"] == "MISSING",
            "Missing session status incorrect"
        )

        print("Missing checkpoint display: PASS")

        cp.atomic_save(
            path,
            cp.replay_prefix(
                stream, 93, source_hash
            )
        )

        # Match the reusable session runner's
        # lock-file convention.
        Path(str(path) + ".lock").touch()

        partial = inspect_process(path)

        require(
            partial["session"]["status"]
            == "IDLE_RECOVERABLE"
            and partial["session"]["committed_transitions"]
            == 93
            and partial["session"]["remaining_transitions"]
            == 563,
            "Recoverable session display incorrect"
        )

        print("Recoverable progress display: PASS")

        cp.atomic_save(
            path,
            cp.replay_prefix(
                stream, 656, source_hash
            )
        )

        complete = inspect_process(path)

        require(
            complete["session"]["status"] == "COMPLETE_IDLE"
            and complete["session"]["committed_transitions"]
            == 656
            and complete["session"]["recorded_breakouts"]
            == 328
            and complete["session"]["confirmed_reversals"]
            == 155
            and complete["session"]["no_failure_within_horizon"]
            == 155
            and complete["session"]["session_end_censored"]
            == 18,
            "Completed session display incorrect"
        )

        print("Completed checkpoint display: PASS")

        corrupt = folder / "corrupt.json"

        altered = json.loads(
            path.read_text()
        )
        altered["payload"]["cursor"] = 1

        corrupt.write_text(
            json.dumps(altered)
        )

        invalid = inspect_process(
            corrupt,
            expected_code=2
        )

        require(
            invalid["session"]["status"]
            == "INVALID_CHECKPOINT",
            "Corrupt checkpoint status incorrect"
        )

        print("Corrupt checkpoint warning: PASS")

        text_result = subprocess.run(
            [
                sys.executable,
                str(DASHBOARD),
                "--checkpoint",
                str(path)
            ],
            capture_output=True,
            text=True,
            timeout=20
        )

        require(
            text_result.returncode == 0
            and "JAHVASCRIPT | RESEARCH OPERATIONS"
                in text_result.stdout
            and "Order execution: DISABLED"
                in text_result.stdout,
            "Human-readable dashboard unavailable"
        )

        print("Terminal dashboard rendering: PASS")

    after = {
        str(path): hashlib.sha256(
            path.read_bytes()
        ).hexdigest()
        for path in PROTECTED
    }

    require(
        before == after,
        "Protected source file changed"
    )

    print("\nSource fingerprint verification: PASS")
    print("Market-state verification: PASS")
    print("Replay timeline verification: PASS")
    print("Session status integration: PASS")
    print("Safety state display: PASS")
    print("Read-only source inspection: PASS")
    print("Temporary checkpoint fixtures: CLEANED UP")
    print("Persistent live session created: NO")

    print("\nRESEARCH DASHBOARD AUDIT PASSED")
    print("WEBULL REQUESTS: NONE")
    print("AI REQUESTS: NONE")
    print("ORDER EXECUTION: DISABLED")


if __name__ == "__main__":
    main()
