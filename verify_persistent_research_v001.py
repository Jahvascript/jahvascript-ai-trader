"""Verify the first permanent JahvaScript research session.

Refuses to replace an existing checkpoint.
Runs control commands in separate Python processes.
"""

import hashlib
import os
import subprocess
import sys
from collections import Counter
from pathlib import Path

import replay_checkpoint_v001 as cp
from research_health_v001 import inspect

ROOT = Path(__file__).resolve().parent
JOURNAL = ROOT / "journal"
CONTROL = ROOT / "research_control_v001.py"
CHECKPOINT = JOURNAL / "research_session_v001.json"

PROTECTED = [
    JOURNAL / "datasets/M5_SPY_QQQ_v001.json",
    JOURNAL / "expanded_research_v001.json",
    JOURNAL / "rvol_clock_normalized_v001.json",
    JOURNAL / "market_state_v001.json",
    JOURNAL / "market_replay_v001.json",
    JOURNAL / "market_replay_timeline_audit_v001.json"
]


def require(condition, message):
    if not condition:
        raise ValueError(
            "PERSISTENT SESSION AUDIT STOPPED: " + message
        )


def run(*args):
    result = subprocess.run(
        [sys.executable, str(CONTROL), *args],
        capture_output=True,
        text=True,
        timeout=30
    )

    require(
        result.returncode == 0,
        "Command failed: "
        + " ".join(args)
        + "\n"
        + result.stdout
        + result.stderr
    )

    return result.stdout


def hashes():
    return {
        str(path): hashlib.sha256(
            path.read_bytes()
        ).hexdigest()
        for path in PROTECTED
    }


def main():
    print(
        "\n=== JAHVASCRIPT PERSISTENT RESEARCH ACTIVATION ===",
        flush=True
    )

    require(
        "journal/" in (
            ROOT / ".gitignore"
        ).read_text().splitlines(),
        "Research journal must be gitignored"
    )

    # This is an activation test, not a reset function.
    # Never overwrite an existing persistent session.
    require(
        not CHECKPOINT.exists(),
        "Permanent checkpoint already exists. "
        "No files changed. Inspect with: "
        "python research_control_v001.py status"
    )

    require(
        inspect(CHECKPOINT)["status"] == "MISSING",
        "Unexpected pre-activation session status"
    )

    before = hashes()
    stream, source_hash = cp.load_verified_stream(ROOT)

    require(
        len(stream) == 656,
        "Unexpected audited transition count"
    )

    print("Verified historical transition source: PASS")

    # First process: deliberately stop after 93 transitions.
    first = run("step", "--count", "93")

    require(
        "PARTIAL SESSION: SAFELY CHECKPOINTED" in first,
        "First process did not stop at a safe checkpoint"
    )

    partial = inspect(CHECKPOINT)

    require(
        partial["status"] == "IDLE_RECOVERABLE"
        and partial["committed_transitions"] == 93
        and partial["remaining_transitions"] == 563
        and partial["writer_lock"] == "FREE",
        "Recoverable session status incorrect"
    )

    partial_state = cp.restore(
        CHECKPOINT, stream, source_hash
    )

    require(
        partial_state["cursor"] == 93,
        "Committed checkpoint cursor mismatch"
    )

    print("Permanent checkpoint created: PASS")
    print("First session committed: 93 / 656")
    print("Dashboard recoverable status: PASS")

    # Second process: resume from the committed cursor.
    second = run("resume")

    require(
        "SESSION READY | recovered cursor: 93" in second
        and "FINAL STATE: VERIFIED" in second,
        "Second process did not resume correctly"
    )

    finished = cp.restore(
        CHECKPOINT, stream, source_hash
    )

    expected = cp.replay_prefix(
        stream, len(stream), source_hash
    )

    require(
        finished == expected,
        "Final state differs from independent full replay"
    )

    require(
        len(finished["records"]) == 328
        and Counter(
            record["outcome"]
            for record in finished["records"].values()
        ) == Counter({
            "CONFIRMED_REVERSAL": 155,
            "NO_FAILURE_WITHIN_HORIZON": 155,
            "CENSORED_SESSION_END": 18
        }),
        "Final historical outcome totals differ"
    )

    print("Second process recovered cursor 93: PASS")
    print("Completed session: 656 / 656")
    print("Historical events recovered: 328")
    print("Final state fingerprint: MATCH")

    complete = inspect(CHECKPOINT)

    require(
        complete["status"] == "COMPLETE_IDLE"
        and complete["remaining_transitions"] == 0
        and complete["pending_outcomes"] == 0,
        "Completed dashboard status incorrect"
    )

    print("Dashboard COMPLETE_IDLE status: PASS")

    # A completed session can be resumed harmlessly.
    checkpoint_hash = hashlib.sha256(
        CHECKPOINT.read_bytes()
    ).hexdigest()

    third = run("resume")

    require(
        "SESSION READY | recovered cursor: 656" in third
        and "FINAL STATE: VERIFIED" in third,
        "Completed-session resume failed"
    )

    require(
        hashlib.sha256(
            CHECKPOINT.read_bytes()
        ).hexdigest() == checkpoint_hash,
        "Completed session changed on repeat resume"
    )

    print("Repeat resume idempotency: PASS")

    require(
        os.stat(CHECKPOINT).st_mode & 0o777 == 0o600,
        "Checkpoint file permissions are not private"
    )

    require(
        hashes() == before,
        "Protected historical research sources changed"
    )

    print("Checkpoint file permissions: 600")
    print("Protected historical files: UNCHANGED")
    print("Historical replay only: YES")
    print("Live market feed: NOT CONNECTED")
    print("Independent source-price verification: NO")
    print("Out-of-sample predictive validation: NO")

    print(
        "\nPERSISTENT HISTORICAL SESSION ACTIVATION PASSED"
    )
    print("WEBULL REQUESTS: NONE")
    print("AI REQUESTS: NONE")
    print("ORDER EXECUTION: DISABLED")


if __name__ == "__main__":
    main()
