"""Exercise historical checkpoint resumption across real Python processes."""

import copy
import hashlib
import json
import subprocess
import sys
import tempfile
from collections import Counter
from pathlib import Path

import replay_checkpoint_v001 as checkpoint

ROOT = Path(__file__).resolve().parent
CLI = ROOT / "replay_checkpoint_v001.py"
JOURNAL = ROOT / "journal"

PROTECTED = [
    JOURNAL / "market_replay_v001.json",
    JOURNAL / "market_replay_timeline_audit_v001.json",
    JOURNAL / "datasets/M5_SPY_QQQ_v001.json"
]


def require(ok, message):
    if not ok:
        raise ValueError(
            "PROCESS RECOVERY AUDIT FAILED: " + message
        )


def invoke(
    action,
    path,
    count=None,
    crash_after=None,
    expected=0
):
    argv = [
        sys.executable,
        str(CLI),
        action,
        "--checkpoint",
        str(path)
    ]

    if count is not None:
        argv += ["--count", str(count)]

    if crash_after is not None:
        argv += [
            "--crash-after",
            str(crash_after)
        ]

    result = subprocess.run(
        argv,
        text=True,
        capture_output=True
    )

    require(
        result.returncode == expected,
        f"{action} returned {result.returncode}, "
        f"expected {expected}: "
        f"{result.stdout} {result.stderr}"
    )

    return result.stdout


def main():
    print(
        "\n=== JAHVASCRIPT CROSS-PROCESS RECOVERY AUDIT ==="
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

    stream, source_hash = (
        checkpoint.load_verified_stream(ROOT)
    )

    require(
        len(stream) == 656,
        "Unexpected stream size"
    )

    with tempfile.TemporaryDirectory(
        prefix="process_recovery_",
        dir=JOURNAL
    ) as directory:
        folder = Path(directory)

        # Uninterrupted baseline.
        uninterrupted = (
            folder / "uninterrupted.json"
        )

        invoke("init", uninterrupted)

        final_output = invoke(
            "advance",
            uninterrupted,
            count=656
        )

        require(
            "FINAL STATE: VERIFIED" in final_output,
            "Uninterrupted process did not finish"
        )

        baseline = checkpoint.restore(
            uninterrupted,
            stream,
            source_hash
        )

        final_hash = checkpoint.digest(
            checkpoint.canonical(baseline)
        )

        print(
            "Separate-process uninterrupted baseline: PASS"
        )

        # Each advance runs in a separate child process.
        # Exit 77 deliberately simulates abrupt termination.
        scenarios = [
            (119, 93),
            (497, 496),
            (655, 651)
        ]

        for crash_after, saved_cursor in scenarios:
            path = (
                folder / f"restart_{crash_after}.json"
            )

            invoke("init", path)

            invoke(
                "advance",
                path,
                count=656,
                crash_after=crash_after,
                expected=77
            )

            status = invoke("status", path)

            require(
                (
                    f"COMMITTED TRANSITIONS: "
                    f"{saved_cursor} / 656"
                ) in status,
                "Unexpected post-crash checkpoint cursor"
            )

            recovered = checkpoint.restore(
                path,
                stream,
                source_hash
            )

            require(
                recovered["cursor"] == saved_cursor,
                "Uncommitted changes survived process exit"
            )

            output = invoke(
                "advance",
                path,
                count=656
            )

            require(
                "FINAL STATE: VERIFIED" in output,
                "Restarted process did not complete"
            )

            finished = checkpoint.restore(
                path,
                stream,
                source_hash
            )

            require(
                checkpoint.digest(
                    checkpoint.canonical(finished)
                ) == final_hash,
                "Restart changed final state"
            )

            print(
                f"Process killed at {crash_after:3} "
                f"| last durable transition "
                f"{saved_cursor:3} | PASS"
            )

        # Redelivering an existing event cannot alter it.
        duplicated = copy.deepcopy(baseline)
        end_message = stream[-1]

        before_records = checkpoint.digest(
            checkpoint.canonical(
                duplicated["records"]
            )
        )

        changed = checkpoint.apply(
            duplicated,
            end_message
        )

        require(
            changed is False
            and checkpoint.digest(
                checkpoint.canonical(
                    duplicated["records"]
                )
            ) == before_records,
            "Duplicate transition was not idempotent"
        )

        print("Duplicate transition idempotency: PASS")

        # Checkpoint corruption detection.
        corruption = folder / "corrupted.json"
        altered = json.loads(
            uninterrupted.read_text()
        )

        altered["payload"]["cursor"] = 1
        corruption.write_text(
            json.dumps(altered)
        )

        try:
            checkpoint.restore(
                corruption,
                stream,
                source_hash
            )
        except ValueError:
            pass
        else:
            raise ValueError(
                "Damaged checkpoint accepted"
            )

        print("Corrupt checkpoint rejection: PASS")

        # Even a recomputed checksum cannot make
        # an incorrect committed prefix acceptable.
        altered["sha256"] = checkpoint.digest(
            checkpoint.canonical(
                altered["payload"]
            )
        )

        corruption.write_text(
            json.dumps(altered)
        )

        try:
            checkpoint.restore(
                corruption,
                stream,
                source_hash
            )
        except ValueError:
            pass
        else:
            raise ValueError(
                "Forged cursor accepted"
            )

        print("Invalid committed-prefix rejection: PASS")

        # Reject checkpoints from changed data sources.
        altered = json.loads(
            uninterrupted.read_text()
        )

        altered["payload"]["source_sha256"] = (
            "wrong-source-version"
        )

        altered["sha256"] = checkpoint.digest(
            checkpoint.canonical(
                altered["payload"]
            )
        )

        corruption.write_text(
            json.dumps(altered)
        )

        try:
            checkpoint.restore(
                corruption,
                stream,
                source_hash
            )
        except ValueError:
            pass
        else:
            raise ValueError(
                "Wrong source checkpoint accepted"
            )

        print("Changed source-version rejection: PASS")

    after = {
        str(path): hashlib.sha256(
            path.read_bytes()
        ).hexdigest()
        for path in PROTECTED
    }

    require(
        before == after,
        "Protected inputs changed"
    )

    require(
        Counter(
            row["outcome"]
            for row in baseline["records"].values()
        ) == Counter({
            "CONFIRMED_REVERSAL": 155,
            "NO_FAILURE_WITHIN_HORIZON": 155,
            "CENSORED_SESSION_END": 18
        }),
        "Final outcomes differ"
    )

    print("\nSeparate Python process restarts: 3/3 PASS")
    print("Final recovered events: 328")
    print("Final processed transitions: 656")
    print("Durable state fingerprints: MATCH")
    print("Temporary checkpoints: CLEANED UP")
    print("Protected historical files: UNCHANGED")
    print("Live feed reliability: NOT VERIFIED")
    print("Concurrent checkpoint writers: NOT SUPPORTED")
    print("Out-of-sample predictive test: NONE")

    print("\nCROSS-PROCESS RECOVERY AUDIT PASSED")
    print("WEBULL REQUESTS: NONE")
    print("AI REQUESTS: NONE")
    print("ORDER EXECUTION: DISABLED")


if __name__ == "__main__":
    main()
