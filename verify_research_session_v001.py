"""Verify single-writer protection and process shutdowns."""

import hashlib
import json
import signal
import subprocess
import sys
import tempfile
import time
from pathlib import Path

import replay_checkpoint_v001 as cp

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
            "SESSION AUDIT FAILED: " + message
        )


def command(path, *extra):
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
            return

        if process.poll() is not None:
            out, err = process.communicate()
            raise ValueError(
                "Child stopped before pause: "
                + out + err
            )

        time.sleep(0.03)

    raise ValueError(
        "Timed out waiting for research-session pause"
    )


def run_to_completion(path, stream, source_hash, expected):
    result = subprocess.run(
        command(path),
        capture_output=True,
        text=True,
        timeout=20
    )

    require(
        result.returncode == 0
        and "FINAL STATE: VERIFIED" in result.stdout,
        "Resume failed: "
        + result.stdout + result.stderr
    )

    state = cp.restore(
        path, stream, source_hash
    )

    require(
        cp.digest(cp.canonical(state)) == expected,
        "Resumed state differs from uninterrupted replay"
    )


def start_paused(path, marker, pause_after):
    process = subprocess.Popen(
        command(
            path,
            "--pause-after", pause_after,
            "--pause-marker", marker,
            "--pause-seconds", 12
        ),
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True
    )

    try:
        wait_for_marker(process, marker)
    except BaseException:
        if process.poll() is None:
            process.kill()
        process.communicate()
        raise

    return process


def main():
    print(
        "\n=== JAHVASCRIPT PERSISTENT SESSION AUDIT ===",
        flush=True
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
        "Unexpected transition count"
    )

    baseline = cp.replay_prefix(
        stream, len(stream), source_hash
    )

    expected = cp.digest(
        cp.canonical(baseline)
    )

    with tempfile.TemporaryDirectory(
        prefix="safe_session_",
        dir=JOURNAL
    ) as directory:
        folder = Path(directory)

        # TEST 1: A running writer blocks another writer.
        graceful_path = folder / "graceful.json"
        graceful_marker = folder / "graceful.ready"

        writer = start_paused(
            graceful_path,
            graceful_marker,
            17
        )

        try:
            state = cp.restore(
                graceful_path,
                stream,
                source_hash
            )

            require(
                state["cursor"] == 0,
                "Unexpected early checkpoint"
            )

            contender = subprocess.run(
                command(graceful_path),
                capture_output=True,
                text=True,
                timeout=10
            )

            require(
                contender.returncode == 73
                and "SESSION LOCK HELD" in contender.stderr,
                "Concurrent writer was not rejected"
            )

            print(
                "Concurrent writer rejection: PASS",
                flush=True
            )

            # TEST 2: SIGTERM saves the pending partial batch.
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

        state = cp.restore(
            graceful_path,
            stream,
            source_hash
        )

        require(
            state["cursor"] == 17,
            "Graceful termination did not flush cursor 17"
        )

        print(
            "SIGTERM checkpoint flush at 17: PASS",
            flush=True
        )

        run_to_completion(
            graceful_path,
            stream,
            source_hash,
            expected
        )

        print(
            "Resume after graceful termination: PASS",
            flush=True
        )

        # TEST 3: SIGKILL cannot save uncommitted memory.
        forced_path = folder / "forced.json"
        forced_marker = folder / "forced.ready"

        victim = start_paused(
            forced_path,
            forced_marker,
            48
        )

        try:
            committed = cp.restore(
                forced_path,
                stream,
                source_hash
            )

            require(
                committed["cursor"] == 31,
                "Expected last committed cursor 31"
            )

            victim.kill()
            victim.communicate(timeout=10)

            require(
                victim.returncode != 0,
                "Forced termination did not occur"
            )
        finally:
            if victim.poll() is None:
                victim.kill()
                victim.communicate()

        recovered = cp.restore(
            forced_path,
            stream,
            source_hash
        )

        require(
            recovered["cursor"] == 31,
            "Uncommitted work survived SIGKILL"
        )

        print(
            "SIGKILL preserved committed cursor 31: PASS",
            flush=True
        )

        # OS releases the lock when the old process dies.
        run_to_completion(
            forced_path,
            stream,
            source_hash,
            expected
        )

        print(
            "Resume after forced termination: PASS",
            flush=True
        )

    after = {
        str(path): hashlib.sha256(
            path.read_bytes()
        ).hexdigest()
        for path in PROTECTED
    }

    require(
        before == after,
        "Protected research files changed"
    )

    print("\nExclusive macOS file locking: PASS")
    print("Graceful shutdown durability: PASS")
    print("Forced termination recovery: PASS")
    print("Deterministic final state: PASS")
    print("Temporary session files: CLEANED UP")
    print("Original historical files: UNCHANGED")
    print("Multiple cooperating writers: PREVENTED")
    print("Noncooperating writers: NOT PROTECTED")
    print("Live market feed: NOT TESTED")
    print("Out-of-sample trading validity: NOT ESTABLISHED")

    print("\nPERSISTENT RESEARCH SESSION AUDIT PASSED")
    print("WEBULL REQUESTS: NONE")
    print("AI REQUESTS: NONE")
    print("ORDER EXECUTION: DISABLED")


if __name__ == "__main__":
    main()
