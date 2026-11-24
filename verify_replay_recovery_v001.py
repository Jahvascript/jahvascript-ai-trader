"""JahvaScript V001 crash-recovery research test.

Replays independently audited event transitions into
temporary atomic checkpoints. Never places trades.
"""

import copy
import hashlib
import json
import os
import tempfile
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent
JOURNAL = ROOT / "journal"
REPLAY = JOURNAL / "market_replay_v001.json"
AUDIT = JOURNAL / "market_replay_timeline_audit_v001.json"

INTERVAL = 31
FINALS = {
    "CONFIRMED_REVERSAL",
    "NO_FAILURE_WITHIN_HORIZON",
    "CENSORED_SESSION_END"
}


def require(ok, message):
    if not ok:
        raise ValueError("RECOVERY AUDIT FAILED: " + message)


def canonical(obj):
    return json.dumps(
        obj, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")


def fingerprint(obj):
    return hashlib.sha256(canonical(obj)).hexdigest()


def timestamp(value):
    dt = datetime.fromisoformat(value)
    require(
        dt.tzinfo is not None
        and dt.utcoffset() is not None,
        "Transition timestamp has no timezone"
    )
    return dt.astimezone(timezone.utc)


def load_messages():
    replay_bytes = REPLAY.read_bytes()
    replay_hash = hashlib.sha256(
        replay_bytes
    ).hexdigest()

    replay = json.loads(replay_bytes)
    audit = json.loads(AUDIT.read_text())

    require(
        audit.get("replay_sha256") == replay_hash
        and audit.get("events_verified") == 328
        and audit.get("transitions_verified") == 656
        and audit.get("event_timing_verified") is True
        and audit.get("execution_enabled") is False
        and audit.get("trade_authorization") is False,
        "Independent timeline audit not verified"
    )

    require(
        replay.get("replay_is_historical_not_live") is True
        and replay.get("out_of_sample") is False
        and replay.get("execution_enabled") is False
        and replay.get("trade_authorization") is False
        and replay.get("trade_signal") is False,
        "Unsafe replay configuration"
    )

    actual = []
    expected = []
    events = {}

    for symbol in ("SPY", "QQQ"):
        section = replay["symbols"][symbol]

        for event in section["events"]:
            key = event["id"]

            require(
                key not in events
                and event["symbol"] == symbol
                and event["trade_signal"] is False
                and event["outcome"] in FINALS,
                "Invalid or duplicate event"
            )

            observed = event["observed_at_utc"]
            terminal = event["outcome_known_at_utc"]

            require(
                timestamp(terminal) >= timestamp(observed),
                "Outcome timestamp precedes observation"
            )

            events[key] = event

            expected.extend([
                (key, observed, "BREAKOUT_OBSERVED"),
                (key, terminal, event["outcome"])
            ])

        for item in section["transitions"]:
            require(
                item["state"] in FINALS
                | {"BREAKOUT_OBSERVED"},
                "Unknown transition"
            )

            timestamp(item["at_utc"])

            actual.append((
                item["id"],
                item["at_utc"],
                item["state"]
            ))

    require(
        len(events) == 328
        and len(actual) == 656
        and Counter(actual) == Counter(expected),
        "Transition log and audited events disagree"
    )

    messages = sorted(
        actual,
        key=lambda item: (
            timestamp(item[1]),
            0 if item[2] == "BREAKOUT_OBSERVED" else 1,
            item[0]
        )
    )

    require(
        hashlib.sha256(REPLAY.read_bytes()).hexdigest()
        == replay_hash,
        "Replay changed while reading"
    )

    return messages, replay_hash


def new_state(source_hash):
    return {
        "schema": 1,
        "source_sha256": source_hash,
        "cursor": 0,
        "records": {},
        "execution_enabled": False
    }


def apply_transition(state, message):
    event_id, at, action = message
    records = state["records"]
    previous = records.get(event_id)

    if action == "BREAKOUT_OBSERVED":
        if previous is None:
            records[event_id] = {
                "observed_at": at,
                "outcome": None,
                "terminal_at": None
            }
            return True

        require(
            previous["observed_at"] == at,
            "Conflicting breakout observation"
        )
        return False

    require(action in FINALS, "Unknown terminal state")
    require(
        previous is not None,
        "Terminal state arrived before observation"
    )
    require(
        timestamp(at) >= timestamp(
            previous["observed_at"]
        ),
        "Premature terminal state"
    )

    if previous["outcome"] is None:
        previous["outcome"] = action
        previous["terminal_at"] = at
        return True

    require(
        previous["outcome"] == action
        and previous["terminal_at"] == at,
        "Contradictory terminal outcome"
    )
    return False


def advance(state, messages, until, checkpoint=None):
    require(
        state["cursor"] <= until <= len(messages),
        "Invalid replay cursor"
    )

    while state["cursor"] < until:
        message = messages[state["cursor"]]
        apply_transition(state, message)
        state["cursor"] += 1

        if (
            checkpoint is not None
            and state["cursor"] % INTERVAL == 0
        ):
            save(checkpoint, state)

    return state


def save(path, state):
    """Write and sync a complete replacement checkpoint."""
    payload = copy.deepcopy(state)

    envelope = {
        "payload": payload,
        "sha256": fingerprint(payload)
    }

    temporary = path.with_name(path.name + ".tmp")

    with temporary.open("x") as handle:
        json.dump(envelope, handle, sort_keys=True)
        handle.write("\n")
        handle.flush()
        os.fsync(handle.fileno())

    os.replace(temporary, path)

    # Persist directory metadata where supported.
    try:
        fd = os.open(str(path.parent), os.O_RDONLY)
        try:
            os.fsync(fd)
        finally:
            os.close(fd)
    except OSError:
        pass


def restore(path, messages, source_hash):
    envelope = json.loads(path.read_text())
    payload = envelope["payload"]

    require(
        fingerprint(payload) == envelope["sha256"],
        "Checkpoint integrity mismatch"
    )

    require(
        payload["schema"] == 1
        and payload["source_sha256"] == source_hash
        and payload["execution_enabled"] is False,
        "Checkpoint version or source mismatch"
    )

    cursor = payload["cursor"]

    require(
        type(cursor) is int
        and 0 <= cursor <= len(messages),
        "Checkpoint cursor invalid"
    )

    # Independently reconstruct the committed prefix.
    rebuilt = new_state(source_hash)
    advance(rebuilt, messages, cursor)

    require(
        rebuilt == payload,
        "Checkpoint state differs from its event prefix"
    )

    return payload


def outcome_counts(state):
    records = state["records"]

    require(
        len(records) == 328,
        "Unexpected final record count"
    )

    counts = Counter(
        row["outcome"] for row in records.values()
    )

    require(
        counts == Counter({
            "CONFIRMED_REVERSAL": 155,
            "NO_FAILURE_WITHIN_HORIZON": 155,
            "CENSORED_SESSION_END": 18
        }),
        "Final outcome reconciliation failed"
    )

    return counts


def main():
    print("\n=== JAHVASCRIPT REPLAY RECOVERY AUDIT ===")

    require(
        "journal/" in (
            ROOT / ".gitignore"
        ).read_text().splitlines(),
        "Journal must be gitignored"
    )

    messages, source_hash = load_messages()
    print("Verified source events: 328")
    print("Verified source transitions:", len(messages))

    baseline = new_state(source_hash)
    advance(baseline, messages, len(messages))

    counts = outcome_counts(baseline)
    expected_final = fingerprint(baseline)

    print("Uninterrupted replay: PASS")

    recovery_points = (1, 30, 32, 119, 328, 497, 655)

    for crash_after in recovery_points:
        with tempfile.TemporaryDirectory(
            prefix="recovery_test_",
            dir=JOURNAL
        ) as directory:
            checkpoint = Path(directory) / "checkpoint.json"

            initial = new_state(source_hash)
            save(checkpoint, initial)

            working = copy.deepcopy(initial)

            # Process messages until a simulated crash.
            # The last partial batch is not committed.
            advance(
                working,
                messages,
                crash_after,
                checkpoint
            )

            # Simulate restarting the process: discard
            # uncommitted in-memory state.
            recovered = restore(
                checkpoint,
                messages,
                source_hash
            )

            saved_cursor = recovered["cursor"]

            require(
                saved_cursor <= crash_after,
                "Checkpoint advanced beyond commit"
            )

            if saved_cursor > 0:
                before = fingerprint(recovered["records"])

                # Redelivery of an already committed
                # transition must have no effect.
                changed = apply_transition(
                    recovered,
                    messages[saved_cursor - 1]
                )

                require(
                    changed is False
                    and fingerprint(
                        recovered["records"]
                    ) == before,
                    "Duplicate delivery changed state"
                )

            advance(
                recovered,
                messages,
                len(messages),
                checkpoint
            )

            save(checkpoint, recovered)

            finished = restore(
                checkpoint,
                messages,
                source_hash
            )

            require(
                fingerprint(finished) == expected_final,
                "Restart changed final replay result"
            )

            outcome_counts(finished)

            print(
                f"Crash after {crash_after:>3} transitions "
                f"| committed {saved_cursor:>3} "
                "| recovery PASS"
            )

    # Verify that conflicting terminal messages
    # cannot rewrite a completed outcome.
    test_state = copy.deepcopy(baseline)
    first_event = next(iter(test_state["records"]))
    row = test_state["records"][first_event]

    replacement = (
        "CONFIRMED_REVERSAL"
        if row["outcome"] != "CONFIRMED_REVERSAL"
        else "NO_FAILURE_WITHIN_HORIZON"
    )

    before_conflict = fingerprint(test_state)

    try:
        apply_transition(
            test_state,
            (
                first_event,
                row["terminal_at"],
                replacement
            )
        )
    except ValueError:
        pass
    else:
        raise ValueError(
            "Conflicting terminal outcome accepted"
        )

    require(
        fingerprint(test_state) == before_conflict,
        "Conflict check mutated final state"
    )

    # Test damaged checkpoint detection using
    # a separate temporary file.
    with tempfile.TemporaryDirectory(
        prefix="checkpoint_integrity_",
        dir=JOURNAL
    ) as directory:
        path = Path(directory) / "checkpoint.json"
        save(path, baseline)

        altered = json.loads(path.read_text())
        altered["payload"]["cursor"] = 1
        path.write_text(json.dumps(altered))

        try:
            restore(path, messages, source_hash)
        except ValueError:
            pass
        else:
            raise ValueError(
                "Damaged checkpoint was accepted"
            )

    print("\n--- FINAL RECONCILIATION ---")
    print("Recovered breakout records:", len(baseline["records"]))

    for name in (
        "CONFIRMED_REVERSAL",
        "NO_FAILURE_WITHIN_HORIZON",
        "CENSORED_SESSION_END"
    ):
        print(name + ":", counts[name])

    print("\nSimulated restart scenarios:", len(recovery_points))
    print("Atomic checkpoint replacement: PASS")
    print("Committed-prefix reconstruction: PASS")
    print("Duplicate transition handling: PASS")
    print("Contradictory outcome rejection: PASS")
    print("Damaged checkpoint detection: PASS")
    print("Deterministic final state: PASS")
    print("Temporary test checkpoints: CLEANED UP")
    print("Original replay and datasets: UNCHANGED")
    print("Live restart behavior: NOT YET VERIFIED")
    print("Out-of-sample evaluation: NOT PERFORMED")
    print("Source prices independently verified: NO")

    print("\nREPLAY RECOVERY AUDIT PASSED")
    print("WEBULL REQUESTS: NONE")
    print("AI REQUESTS: NONE")
    print("ORDER EXECUTION: DISABLED")


if __name__ == "__main__":
    main()
