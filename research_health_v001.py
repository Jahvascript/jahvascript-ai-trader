"""Read-only health inspection for JahvaScript historical sessions.

Validates committed checkpoints against the audited source.
Never creates checkpoints, requests data, or executes orders.
"""

import argparse
import fcntl
import json
import os
from collections import Counter
from pathlib import Path

import replay_checkpoint_v001 as cp


def writer_lock_status(checkpoint):
    lock_path = Path(str(checkpoint) + ".lock")

    if not lock_path.exists():
        return "LOCK_FILE_MISSING"

    fd = os.open(str(lock_path), os.O_RDONLY)

    try:
        try:
            fcntl.flock(
                fd,
                fcntl.LOCK_EX | fcntl.LOCK_NB
            )
        except BlockingIOError:
            return "HELD"
        else:
            fcntl.flock(fd, fcntl.LOCK_UN)
            return "FREE"
    finally:
        os.close(fd)


def inspect(checkpoint):
    checkpoint = Path(checkpoint).resolve()

    if not checkpoint.is_file():
        return {
            "status": "MISSING",
            "checkpoint": str(checkpoint),
            "execution_enabled": False
        }

    root = Path(__file__).resolve().parent
    stream, source_hash = cp.load_verified_stream(root)

    lock = writer_lock_status(checkpoint)

    try:
        state = cp.restore(
            checkpoint,
            stream,
            source_hash
        )

    except (
        ValueError, KeyError, TypeError,
        OSError, json.JSONDecodeError
    ) as error:
        return {
            "status": "INVALID_CHECKPOINT",
            "checkpoint": str(checkpoint),
            "writer_lock": lock,
            "error": str(error),
            "execution_enabled": False
        }

    cursor = state["cursor"]
    records = state["records"]

    terminal_counts = Counter(
        record["outcome"]
        for record in records.values()
        if record["outcome"] is not None
    )

    pending = sum(
        record["outcome"] is None
        for record in records.values()
    )

    if lock == "HELD":
        status = "ACTIVE_WRITER"
    elif lock == "FREE" and cursor == len(stream):
        status = "COMPLETE_IDLE"
    elif lock == "FREE":
        status = "IDLE_RECOVERABLE"
    else:
        status = "LOCK_STATUS_UNKNOWN"

    last_committed = (
        stream[cursor - 1][1]
        if cursor > 0
        else None
    )

    return {
        "status": status,
        "checkpoint": str(checkpoint),
        "writer_lock": lock,
        "committed_transitions": cursor,
        "total_transitions": len(stream),
        "remaining_transitions": len(stream) - cursor,
        "progress_percent": round(
            100 * cursor / len(stream), 1
        ),
        "recorded_breakouts": len(records),
        "pending_outcomes": pending,
        "confirmed_reversals": terminal_counts[
            "CONFIRMED_REVERSAL"
        ],
        "no_failure_within_horizon": terminal_counts[
            "NO_FAILURE_WITHIN_HORIZON"
        ],
        "session_end_censored": terminal_counts[
            "CENSORED_SESSION_END"
        ],
        "last_committed_transition_utc": last_committed,
        "checkpoint_integrity": "PASS",
        "historical_replay": True,
        "live_feed_verified": False,
        "execution_enabled": False
    }


def display(result):
    print("\n=== JAHVASCRIPT RESEARCH SESSION HEALTH ===")
    print("STATUS:", result["status"])
    print("CHECKPOINT:", result["checkpoint"])

    if "writer_lock" in result:
        print("WRITER LOCK:", result["writer_lock"])

    if "committed_transitions" in result:
        print(
            "COMMITTED PROGRESS:",
            result["committed_transitions"],
            "/",
            result["total_transitions"]
        )

        print(
            "PROGRESS:",
            str(result["progress_percent"]) + "%"
        )

        print(
            "REMAINING TRANSITIONS:",
            result["remaining_transitions"]
        )

        print(
            "BREAKOUT RECORDS:",
            result["recorded_breakouts"]
        )

        print(
            "PENDING OUTCOMES:",
            result["pending_outcomes"]
        )

        print(
            "CONFIRMED REVERSALS:",
            result["confirmed_reversals"]
        )

        print(
            "HORIZON ELAPSED:",
            result["no_failure_within_horizon"]
        )

        print(
            "SESSION-END CENSORED:",
            result["session_end_censored"]
        )

        print(
            "LAST COMMITTED HISTORICAL TIMESTAMP:",
            result["last_committed_transition_utc"]
        )

        print(
            "CHECKPOINT INTEGRITY:",
            result["checkpoint_integrity"]
        )

    if "error" in result:
        print("INTEGRITY ERROR:", result["error"])

    print(
        "IN-MEMORY UNCOMMITTED WORK: NOT OBSERVABLE"
    )
    print(
        "HISTORICAL TIMESTAMP IS NOT LIVE-FEED FRESHNESS"
    )
    print("LIVE FEED: NOT VERIFIED")
    print("ORDER EXECUTION: DISABLED")


def main():
    parser = argparse.ArgumentParser(
        description=__doc__
    )

    parser.add_argument(
        "--checkpoint",
        type=Path,
        required=True
    )

    args = parser.parse_args()
    result = inspect(args.checkpoint)
    display(result)

    if result["status"] == "INVALID_CHECKPOINT":
        raise SystemExit(2)


if __name__ == "__main__":
    main()
