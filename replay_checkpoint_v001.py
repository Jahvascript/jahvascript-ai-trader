"""Reusable, offline JahvaScript historical-transition checkpoint store.

One writer at a time. No live feeds, networks, AI, or orders.
"""

import argparse
import hashlib
import json
import os
import sys
import tempfile
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

BATCH = 31
FINAL = {
    "CONFIRMED_REVERSAL",
    "NO_FAILURE_WITHIN_HORIZON",
    "CENSORED_SESSION_END"
}


def check(condition, message):
    if not condition:
        raise ValueError("CHECKPOINT STOPPED: " + message)


def digest(blob):
    return hashlib.sha256(blob).hexdigest()


def canonical(obj):
    return json.dumps(
        obj, sort_keys=True, separators=(",", ":")
    ).encode()


def parse(value):
    dt = datetime.fromisoformat(value)
    check(
        dt.tzinfo is not None
        and dt.utcoffset() is not None,
        "Timestamp must have timezone"
    )
    return dt.astimezone(timezone.utc)


def load_verified_stream(root):
    replay_path = root / "journal/market_replay_v001.json"
    audit_path = (
        root / "journal/market_replay_timeline_audit_v001.json"
    )
    data_path = (
        root / "journal/datasets/M5_SPY_QQQ_v001.json"
    )

    raw = replay_path.read_bytes()
    source_hash = digest(raw)
    replay = json.loads(raw)
    audit = json.loads(audit_path.read_text())
    data_hash = digest(data_path.read_bytes())

    check(
        audit.get("replay_sha256") == source_hash
        and audit.get("dataset_sha256") == data_hash
        and audit.get("events_verified") == 328
        and audit.get("transitions_verified") == 656
        and audit.get("event_timing_verified") is True
        and audit.get("execution_enabled") is False
        and audit.get("trade_authorization") is False,
        "Independent audit or source fingerprint mismatch"
    )

    check(
        replay.get("dataset_sha256") == data_hash
        and replay.get("replay_is_historical_not_live") is True
        and replay.get("out_of_sample") is False
        and replay.get("trade_signal") is False
        and replay.get("execution_enabled") is False
        and replay.get("trade_authorization") is False,
        "Replay configuration not verified or unsafe"
    )

    expected = []
    received = []
    identifiers = set()

    for symbol in ("SPY", "QQQ"):
        block = replay["symbols"][symbol]

        for event in block["events"]:
            event_id = event["id"]

            check(
                event_id not in identifiers
                and event["symbol"] == symbol
                and event["outcome"] in FINAL
                and event.get("trade_signal") is False,
                "Invalid source event"
            )

            identifiers.add(event_id)

            observed = event["observed_at_utc"]
            completed = event["outcome_known_at_utc"]

            check(
                parse(completed) >= parse(observed),
                "Outcome precedes observation"
            )

            expected.extend([
                (
                    event_id,
                    observed,
                    "BREAKOUT_OBSERVED"
                ),
                (
                    event_id,
                    completed,
                    event["outcome"]
                )
            ])

        for item in block["transitions"]:
            check(
                item["state"] in FINAL | {"BREAKOUT_OBSERVED"},
                "Unknown transition state"
            )

            parse(item["at_utc"])

            received.append((
                item["id"],
                item["at_utc"],
                item["state"]
            ))

    check(
        len(identifiers) == 328
        and len(received) == 656
        and Counter(expected) == Counter(received),
        "Transition log and audited events disagree"
    )

    ordered = sorted(
        received,
        key=lambda t: (
            parse(t[1]),
            0 if t[2] == "BREAKOUT_OBSERVED" else 1,
            t[0]
        )
    )

    return ordered, source_hash


def fresh(source_hash):
    return {
        "schema": 1,
        "source_sha256": source_hash,
        "cursor": 0,
        "records": {},
        "execution_enabled": False
    }


def apply(state, transition):
    event_id, at, action = transition
    record = state["records"].get(event_id)

    if action == "BREAKOUT_OBSERVED":
        if record is None:
            state["records"][event_id] = {
                "observed_at": at,
                "outcome": None,
                "terminal_at": None
            }
            return True

        check(
            record["observed_at"] == at,
            "Contradictory observation"
        )
        return False

    check(
        action in FINAL and record is not None,
        "Unknown or prematurely terminal transition"
    )

    check(
        parse(at) >= parse(record["observed_at"]),
        "Outcome timestamp predates observation"
    )

    if record["outcome"] is None:
        record["outcome"] = action
        record["terminal_at"] = at
        return True

    check(
        record["outcome"] == action
        and record["terminal_at"] == at,
        "Contradictory terminal outcome"
    )
    return False


def replay_prefix(stream, count, source_hash):
    state = fresh(source_hash)

    for message in stream[:count]:
        apply(state, message)
        state["cursor"] += 1

    return state


def atomic_save(path, state):
    check(
        path.parent.is_dir(),
        "Checkpoint parent directory missing"
    )

    payload = {
        "payload": state,
        "sha256": digest(canonical(state))
    }

    fd, temp_path = tempfile.mkstemp(
        prefix=".jahvascript-checkpoint-",
        suffix=".tmp",
        dir=str(path.parent)
    )

    try:
        with os.fdopen(fd, "wb") as file:
            file.write(canonical(payload) + b"\n")
            file.flush()
            os.fsync(file.fileno())

        os.chmod(temp_path, 0o600)
        os.replace(temp_path, path)

        try:
            directory_fd = os.open(
                str(path.parent),
                os.O_RDONLY
            )
            try:
                os.fsync(directory_fd)
            finally:
                os.close(directory_fd)
        except OSError:
            pass

    finally:
        if os.path.exists(temp_path):
            os.unlink(temp_path)


def restore(path, stream, source_hash):
    envelope = json.loads(path.read_text())

    check(
        set(envelope) == {"payload", "sha256"},
        "Invalid envelope"
    )

    state = envelope["payload"]

    check(
        digest(canonical(state)) == envelope["sha256"],
        "Checkpoint checksum mismatch"
    )

    check(
        state.get("schema") == 1
        and state.get("source_sha256") == source_hash
        and state.get("execution_enabled") is False,
        "Checkpoint version/source/safety mismatch"
    )

    cursor = state.get("cursor")

    check(
        type(cursor) is int
        and 0 <= cursor <= len(stream),
        "Invalid transition cursor"
    )

    check(
        state == replay_prefix(
            stream, cursor, source_hash
        ),
        "Committed checkpoint disagrees with source prefix"
    )

    return state


def advance(
    path,
    stream,
    source_hash,
    count,
    crash_after=None
):
    check(
        type(count) is int and count >= 0,
        "Invalid advance count"
    )

    state = restore(path, stream, source_hash)
    goal = min(
        len(stream),
        state["cursor"] + count
    )

    processed = 0

    while state["cursor"] < goal:
        apply(
            state,
            stream[state["cursor"]]
        )

        state["cursor"] += 1
        processed += 1

        if state["cursor"] % BATCH == 0:
            atomic_save(path, state)

        # Deliberately terminate without saving
        # the current incomplete batch.
        if (
            crash_after is not None
            and processed == crash_after
        ):
            os._exit(77)

    atomic_save(path, state)

    return restore(path, stream, source_hash)


def counts(state):
    return Counter(
        row["outcome"]
        for row in state["records"].values()
    )


def main():
    parser = argparse.ArgumentParser(
        description=__doc__
    )

    parser.add_argument(
        "action",
        choices=("init", "advance", "status")
    )
    parser.add_argument(
        "--checkpoint",
        required=True,
        type=Path
    )
    parser.add_argument(
        "--count",
        type=int,
        default=656
    )
    parser.add_argument(
        "--crash-after",
        type=int
    )

    args = parser.parse_args()

    root = Path(__file__).resolve().parent
    stream, source_hash = load_verified_stream(root)
    path = args.checkpoint.resolve()

    check(
        path.parent.is_dir(),
        "Checkpoint directory does not exist"
    )

    if args.action == "init":
        check(
            not path.exists(),
            "Checkpoint already exists"
        )

        atomic_save(
            path,
            fresh(source_hash)
        )

        state = restore(
            path, stream, source_hash
        )

    elif args.action == "advance":
        check(
            args.crash_after is None
            or 1 <= args.crash_after <= args.count,
            "Invalid crash position"
        )

        state = advance(
            path,
            stream,
            source_hash,
            args.count,
            args.crash_after
        )

    else:
        check(
            args.crash_after is None,
            "Crash only applies to advance"
        )

        state = restore(
            path, stream, source_hash
        )

    print("ACTION:", args.action)
    print(
        "COMMITTED TRANSITIONS:",
        state["cursor"],
        "/",
        len(stream)
    )
    print("RECORDS:", len(state["records"]))
    print(
        "STATE FINGERPRINT:",
        digest(canonical(state))
    )

    if state["cursor"] == len(stream):
        summary = counts(state)

        check(
            len(state["records"]) == 328
            and summary == Counter({
                "CONFIRMED_REVERSAL": 155,
                "NO_FAILURE_WITHIN_HORIZON": 155,
                "CENSORED_SESSION_END": 18
            }),
            "Final replay outcomes differ"
        )

        print("FINAL STATE: VERIFIED")

    print("ORDER EXECUTION: DISABLED")


if __name__ == "__main__":
    try:
        main()
    except (
        ValueError,
        KeyError,
        OSError,
        TypeError
    ) as error:
        print(error, file=sys.stderr)
        sys.exit(1)
