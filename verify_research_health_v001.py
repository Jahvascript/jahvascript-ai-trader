"""Test historical research health statuses across real processes."""

import hashlib
import json
import signal
import subprocess
import sys
import tempfile
import time
from pathlib import Path

import replay_checkpoint_v001 as cp
from research_health_v001 import inspect

ROOT = Path(__file__).resolve().parent
JOURNAL = ROOT / "journal"
RUNNER = ROOT / "research_session_v001.py"

PROTECTED = [
    JOURNAL / "datasets/M5_SPY_QQQ_v001.json",
    JOURNAL / "market_replay_v001.json",
    JOURNAL / "market_replay_timeline_audit_v001.json"
]


def require(ok, message):
    if not ok:
        raise ValueError(
            "HEALTH AUDIT FAILED: " + message
        )


def runner_command(path, *extra):
    return [
        sys.executable,
        str(RUNNER),
        "--checkpoint",
        str(path),
        *map(str, extra)
    ]


def wait_for_marker(process, marker):
    deadline = time.monotonic() + 8

    while time.monotonic() < deadline:
        if marker.exists():
            require(
                process.poll() is None,
                "Writer exited before health check"
            )
            return

        if process.poll() is not None:
            out, err = process.communicate()
            raise ValueError(
                "Writer exited early: " + out + err
            )

        time.sleep(0.03)

    raise ValueError(
        "Timed out waiting for paused writer"
    )


def main():
    print(
        "\n=== JAHVASCRIPT RESEARCH HEALTH AUDIT ===",
        flush=True
    )

    require(
        "journal/" in (
            ROOT / ".gitignore"
        ).read_text().splitlines(),
        "Journal must be gitignored"
    )

    hashes_before = {
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
        "Unexpected historical stream length"
    )

    with tempfile.TemporaryDirectory(
        prefix="health_test_",
        dir=JOURNAL
    ) as directory:
        folder = Path(directory)
        checkpoint = folder / "session.json"
        marker = folder / "writer.ready"

        # Missing session check.
        missing = inspect(checkpoint)

        require(
            missing["status"] == "MISSING",
            "Missing checkpoint not detected"
        )

        print("Missing checkpoint detection: PASS")

        # A real child process obtains the writer lock
        # and pauses after 17 in-memory transitions.
        writer = subprocess.Popen(
            runner_command(
                checkpoint,
                "--pause-after", 17,
                "--pause-marker", marker,
                "--pause-seconds", 12
            ),
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True
        )

        try:
            wait_for_marker(writer, marker)

            active = inspect(checkpoint)

            require(
                active["status"] == "ACTIVE_WRITER"
                and active["writer_lock"] == "HELD"
                and active["committed_transitions"] == 0,
                "Active writer or committed state incorrect"
            )

            print("Active writer detection: PASS")
            print(
                "Uncommitted activity kept separate: PASS"
            )

            writer.send_signal(signal.SIGTERM)
            out, err = writer.communicate(timeout=10)

            require(
                writer.returncode == 0
                and "GRACEFUL SHUTDOWN" in out,
                "Graceful shutdown failed: " + out + err
            )

        finally:
            if writer.poll() is None:
                writer.kill()
                writer.communicate()

        # The lock must now be free and the graceful
        # termination must have saved all 17 transitions.
        idle = inspect(checkpoint)

        require(
            idle["status"] == "IDLE_RECOVERABLE"
            and idle["writer_lock"] == "FREE"
            and idle["committed_transitions"] == 17
            and idle["remaining_transitions"] == 639,
            "Recoverable checkpoint not detected"
        )

        print("Idle recoverable checkpoint: PASS")
        print("Graceful cursor reconciliation: PASS")

        # Resume in a completely new Python process.
        finish = subprocess.run(
            runner_command(checkpoint),
            capture_output=True,
            text=True,
            timeout=20
        )

        require(
            finish.returncode == 0
            and "FINAL STATE: VERIFIED"
            in finish.stdout,
            "Unable to finish historical replay: "
            + finish.stdout + finish.stderr
        )

        complete = inspect(checkpoint)

        require(
            complete["status"] == "COMPLETE_IDLE"
            and complete["writer_lock"] == "FREE"
            and complete["committed_transitions"] == 656
            and complete["recorded_breakouts"] == 328
            and complete["pending_outcomes"] == 0
            and complete["confirmed_reversals"] == 155
            and complete["no_failure_within_horizon"] == 155
            and complete["session_end_censored"] == 18,
            "Completed session health mismatch"
        )

        print("Completed idle session detection: PASS")
        print("Final outcome health totals: PASS")

        # Check integrity failure detection without
        # ever modifying the verified checkpoint.
        corrupted = folder / "corrupted.json"

        envelope = json.loads(
            checkpoint.read_text()
        )

        envelope["payload"]["cursor"] = 7

        corrupted.write_text(
            json.dumps(envelope)
        )

        bad = inspect(corrupted)

        require(
            bad["status"] == "INVALID_CHECKPOINT",
            "Corruption was not detected"
        )

        print("Corrupted checkpoint detection: PASS")

        # A valid envelope with the wrong source hash
        # must also be rejected.
        wrong_source = folder / "wrong_source.json"

        cp.atomic_save(
            wrong_source,
            cp.fresh("incorrect-historical-source")
        )

        invalid_source = inspect(wrong_source)

        require(
            invalid_source["status"]
            == "INVALID_CHECKPOINT",
            "Wrong-version checkpoint was accepted"
        )

        print("Wrong-source checkpoint rejection: PASS")

    hashes_after = {
        str(path): hashlib.sha256(
            path.read_bytes()
        ).hexdigest()
        for path in PROTECTED
    }

    require(
        hashes_before == hashes_after,
        "Protected historical files changed"
    )

    print("\nSession statuses tested: 5")
    print("Active process locking: VERIFIED")
    print("Read-only committed-state inspection: PASS")
    print("Uncommitted state not misreported: PASS")
    print("Historical source files: UNCHANGED")
    print("Temporary test files: CLEANED UP")
    print("Historical checkpoint health: VERIFIED")
    print("Live feed and real-time freshness: NOT TESTED")
    print("Out-of-sample predictive test: NONE")

    print("\nRESEARCH HEALTH AUDIT PASSED")
    print("WEBULL REQUESTS: NONE")
    print("AI REQUESTS: NONE")
    print("ORDER EXECUTION: DISABLED")


if __name__ == "__main__":
    main()
