"""JahvaScript single-writer historical research session.

Mac/POSIX file locking; deterministic historical transitions.
No market requests, AI calls, live orders or trading signals.
"""

import argparse
import fcntl
import os
import signal
import sys
import time
from contextlib import contextmanager
from pathlib import Path

import replay_checkpoint_v001 as cp

STOP_REQUESTED = False


def on_shutdown(signum, frame):
    global STOP_REQUESTED
    STOP_REQUESTED = True


@contextmanager
def exclusive_writer(checkpoint):
    lock_path = Path(str(checkpoint) + ".lock")
    fd = os.open(
        str(lock_path),
        os.O_CREAT | os.O_RDWR,
        0o600
    )

    try:
        try:
            fcntl.flock(
                fd,
                fcntl.LOCK_EX | fcntl.LOCK_NB
            )
        except BlockingIOError:
            raise RuntimeError(
                "SESSION LOCK HELD: another writer is active"
            )

        yield

    finally:
        try:
            fcntl.flock(fd, fcntl.LOCK_UN)
        finally:
            os.close(fd)


def main():
    parser = argparse.ArgumentParser(
        description=__doc__
    )

    parser.add_argument(
        "--checkpoint",
        type=Path,
        required=True
    )
    parser.add_argument(
        "--max-transitions",
        type=int,
        default=656
    )

    # Controlled interruption hooks for offline testing.
    parser.add_argument("--pause-after", type=int)
    parser.add_argument("--pause-marker", type=Path)
    parser.add_argument(
        "--pause-seconds",
        type=float,
        default=12.0
    )

    args = parser.parse_args()

    if args.max_transitions < 0:
        raise ValueError("Invalid transition limit")

    if args.pause_after is not None:
        if not (
            1 <= args.pause_after <= args.max_transitions
            and args.pause_marker is not None
            and 0 < args.pause_seconds <= 30
        ):
            raise ValueError("Invalid test pause settings")

    checkpoint = args.checkpoint.resolve()

    if not checkpoint.parent.is_dir():
        raise ValueError(
            "Checkpoint directory does not exist"
        )

    signal.signal(signal.SIGTERM, on_shutdown)
    signal.signal(signal.SIGINT, on_shutdown)

    # The lock covers initialization, recovery, processing,
    # and every checkpoint write.
    with exclusive_writer(checkpoint):
        root = Path(__file__).resolve().parent
        stream, source_hash = cp.load_verified_stream(
            root
        )

        if not checkpoint.exists():
            cp.atomic_save(
                checkpoint,
                cp.fresh(source_hash)
            )

        state = cp.restore(
            checkpoint,
            stream,
            source_hash
        )

        initial_cursor = state["cursor"]

        print(
            "SESSION READY | recovered cursor:",
            initial_cursor,
            flush=True
        )

        goal = min(
            len(stream),
            initial_cursor + args.max_transitions
        )

        processed = 0

        while (
            state["cursor"] < goal
            and not STOP_REQUESTED
        ):
            message = stream[state["cursor"]]

            cp.apply(state, message)
            state["cursor"] += 1
            processed += 1

            if state["cursor"] % cp.BATCH == 0:
                cp.atomic_save(checkpoint, state)

            if processed == args.pause_after:
                args.pause_marker.write_text(
                    str(state["cursor"]) + "\n"
                )

                deadline = (
                    time.monotonic()
                    + args.pause_seconds
                )

                while (
                    time.monotonic() < deadline
                    and not STOP_REQUESTED
                ):
                    time.sleep(0.02)

        # Commit the final incomplete batch, including
        # on graceful SIGTERM or SIGINT.
        cp.atomic_save(checkpoint, state)

        restored = cp.restore(
            checkpoint,
            stream,
            source_hash
        )

        if restored != state:
            raise ValueError(
                "Final checkpoint does not match memory"
            )

        print(
            "COMMITTED CURSOR:",
            restored["cursor"],
            "/",
            len(stream)
        )

        if STOP_REQUESTED:
            print("GRACEFUL SHUTDOWN: CHECKPOINT SAVED")
        elif restored["cursor"] == len(stream):
            counts = cp.counts(restored)

            expected = {
                "CONFIRMED_REVERSAL": 155,
                "NO_FAILURE_WITHIN_HORIZON": 155,
                "CENSORED_SESSION_END": 18
            }

            if (
                len(restored["records"]) != 328
                or dict(counts) != expected
            ):
                raise ValueError(
                    "Final outcomes failed reconciliation"
                )

            print("FINAL STATE: VERIFIED")
        else:
            print("PARTIAL SESSION: SAFELY CHECKPOINTED")

        print("ORDER EXECUTION: DISABLED")


if __name__ == "__main__":
    try:
        main()
    except RuntimeError as error:
        print(str(error), file=sys.stderr)
        sys.exit(73)
    except (
        ValueError, KeyError, OSError, TypeError
    ) as error:
        print(str(error), file=sys.stderr)
        sys.exit(1)
