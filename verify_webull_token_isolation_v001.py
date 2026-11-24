"""Verify Webull token storage isolation with disposable test directories."""

import hashlib
import importlib.metadata
import json
import os
import socket
import tempfile
from pathlib import Path
from unittest.mock import patch

import requests

from forward_collection_schedule_v001 import verify_sources

ROOT = Path(__file__).resolve().parent
REPORT = (
    ROOT / "journal/forward_evaluation_v001/"
    "webull_token_isolation_audit_v001.json"
)
SCHEDULE = (
    ROOT / "journal/forward_evaluation_v001/"
    "collection_schedule_v001.json"
)


def require(ok, reason):
    if not ok:
        raise AssertionError(
            "TOKEN ISOLATION AUDIT FAILED: " + reason
        )


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def deny_network(*args, **kwargs):
    raise AssertionError("NETWORK ACCESS BLOCKED")


def require_isolated_path(path, approved_root):
    """Reject paths outside the designated storage directory."""
    root = Path(approved_root)
    candidate = Path(path)

    if root.is_symlink():
        raise PermissionError(
            "SYMLINKED TOKEN ROOT BLOCKED"
        )

    if candidate.resolve().parent != root.resolve():
        raise PermissionError(
            "TOKEN PATH OUTSIDE APPROVED ROOT"
        )

    if candidate.is_symlink():
        raise PermissionError(
            "SYMLINKED TOKEN FILE BLOCKED"
        )

    return True


def expect_rejection(label, callback):
    try:
        callback()
    except PermissionError:
        print(label + ": REJECTED")
        return

    raise AssertionError(
        "UNSAFE TOKEN PATH ACCEPTED: " + label
    )


def main():
    print(
        "\n=== JAHVASCRIPT WEBULL TOKEN ISOLATION AUDIT ==="
    )

    before = verify_sources()
    require(SCHEDULE.is_file(), "Schedule missing")
    schedule_hash = sha(SCHEDULE)

    version = importlib.metadata.version(
        "webull-openapi-python-sdk"
    )

    require(version == "3.0.3", "SDK version changed")

    from webull.core.client import ApiClient
    from webull.core.http.initializer.token.token_storage import (
        TokenStorage
    )

    import webull.core.http.initializer.token.token_storage as storage_module

    storage_sha = sha(Path(storage_module.__file__))

    print("Webull SDK:", version)
    print("Frozen research fingerprints: VERIFIED")

    original_cwd = Path.cwd()

    with tempfile.TemporaryDirectory(
        prefix="jahvascript_webull_isolation_"
    ) as temporary:

        base = Path(temporary).resolve()

        approved = base / "probe_tokens"
        env_fallback = base / "environment_tokens"

        approved.mkdir(mode=0o700)
        env_fallback.mkdir(mode=0o700)

        # Fictional marker entirely inside disposable storage.
        sentinel = env_fallback / "token.txt"
        sentinel.write_text("SYNTHETIC_DO_NOT_CHANGE\n")
        sentinel_hash = sha(sentinel)

        with patch.object(
            requests.sessions.Session, "send", deny_network
        ), patch.object(
            socket.socket, "connect", deny_network
        ), patch.object(
            socket.socket, "connect_ex", deny_network
        ), patch.dict(
            os.environ,
            {
                "WEBULL_OPENAPI_TOKEN_DIR":
                    str(env_fallback)
            }
        ):
            try:
                os.chdir(base)

                # No genuine credentials are read.
                client = ApiClient(
                    "JAHVASCRIPT_SYNTHETIC_KEY",
                    "JAHVASCRIPT_SYNTHETIC_SECRET",
                    "us"
                )

                client.set_token_dir(str(approved))

                require(
                    Path(client.get_token_dir()).resolve()
                    == approved,
                    "ApiClient ignored isolated token directory"
                )

                storage = TokenStorage(
                    client.get_token_dir()
                )

                isolated_file = Path(
                    storage.get_token_file_path()
                )

                require_isolated_path(
                    isolated_file, approved
                )

                require(
                    not isolated_file.exists(),
                    "Unexpected token file already exists"
                )

                require(
                    sha(sentinel) == sentinel_hash,
                    "Environment token marker was modified"
                )

                print("\n--- SDK STORAGE ISOLATION ---")
                print("Synthetic ApiClient construction: PASS")
                print("Explicit set_token_dir(): PASS")
                print("Token path inside approved directory: PASS")
                print("Environment fallback overridden: PASS")
                print("Existing synthetic token marker: UNCHANGED")
                print("New token files written: NONE")

                # Test the SDK's environment fallback separately.
                fallback_storage = TokenStorage(None)

                fallback_file = Path(
                    fallback_storage.get_token_file_path()
                )

                require_isolated_path(
                    fallback_file, env_fallback
                )

                require(
                    sha(sentinel) == sentinel_hash,
                    "Fallback storage changed existing marker"
                )

                print("Environment fallback path: VERIFIED")

                print("\n--- PATH REJECTION TESTS ---")

                expect_rejection(
                    "Outside approved directory",
                    lambda: require_isolated_path(
                        env_fallback / "token.txt",
                        approved
                    )
                )

                expect_rejection(
                    "Parent-directory escape",
                    lambda: require_isolated_path(
                        approved / ".." / "token.txt",
                        approved
                    )
                )

                linked_root = base / "linked_tokens"
                linked_root.symlink_to(
                    env_fallback,
                    target_is_directory=True
                )

                expect_rejection(
                    "Symlinked storage directory",
                    lambda: require_isolated_path(
                        linked_root / "token.txt",
                        linked_root
                    )
                )

                linked_file = approved / "linked_token.txt"
                linked_file.symlink_to(sentinel)

                expect_rejection(
                    "Symlinked token file",
                    lambda: require_isolated_path(
                        linked_file, approved
                    )
                )

                require(
                    sha(sentinel) == sentinel_hash,
                    "Existing synthetic token marker changed"
                )

            finally:
                os.chdir(original_cwd)

    require(
        verify_sources() == before,
        "Frozen research changed"
    )
    require(
        sha(SCHEDULE) == schedule_hash,
        "Collection schedule changed"
    )
    require(
        sha(Path(storage_module.__file__)) == storage_sha,
        "Installed SDK source changed"
    )

    report = {
        "version": "WEBULL_TOKEN_ISOLATION_AUDIT_V001",
        "sdk_version": version,
        "sdk_token_storage_sha256": storage_sha,
        "collection_schedule_sha256": schedule_hash,
        "research_fingerprints": before,
        "explicit_token_directory": "PASS",
        "environment_override": "PASS",
        "environment_fallback": "PASS",
        "synthetic_existing_file": "UNCHANGED",
        "path_escape_rejections": 4,
        "real_token_files_accessed": False,
        "real_credentials_accessed": False,
        "data_client_instantiated": False,
        "actual_token_manager_executed": False,
        "network_requests": 0,
        "live_webull_schema_verified": False,
        "real_sessions_admitted": 0,
        "trade_authorization": False
    }

    REPORT.parent.mkdir(parents=True, exist_ok=True)

    if REPORT.exists():
        require(
            REPORT.is_file()
            and not REPORT.is_symlink()
            and json.loads(REPORT.read_text()) == report,
            "Existing audit differs; original preserved"
        )
        print("\nExisting isolation audit: VERIFIED")
    else:
        os.umask(0o077)

        with REPORT.open("x") as handle:
            json.dump(report, handle, indent=2)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())

        REPORT.chmod(0o600)
        print("\nNew isolation audit: SAVED")

    print("\n--- FINAL RESULT ---")
    print("SDK token directory isolation: OFFLINE VERIFIED")
    print("Probe path escape policy: VERIFIED")
    print("Actual token initialization: NOT EXECUTED")
    print("DataClient logging isolation: NOT YET VERIFIED")
    print("Live Webull response schema: NOT VERIFIED")
    print("Existing real token files: NOT ACCESSED")
    print("Network requests: NONE")
    print("Research V001: UNCHANGED")
    print("Real sessions admitted: 0")
    print("Order execution: DISABLED")
    print("\nSTEP 15H.65W TOKEN ISOLATION VERIFIED")


if __name__ == "__main__":
    main()
