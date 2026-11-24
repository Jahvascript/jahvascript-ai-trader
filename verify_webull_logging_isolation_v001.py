"""Offline Webull DataClient logging isolation audit."""

import hashlib
import importlib.metadata
import json
import logging
import os
import socket
import tempfile
from pathlib import Path
from unittest.mock import patch

import requests

from forward_collection_schedule_v001 import verify_sources

ROOT = Path(__file__).resolve().parent
FORWARD = ROOT / "journal/forward_evaluation_v001"
REPORT = FORWARD / "webull_logging_isolation_audit_v001.json"
SCHEDULE = FORWARD / "collection_schedule_v001.json"


def require(ok, reason):
    if not ok:
        raise AssertionError(
            "LOGGING ISOLATION AUDIT FAILED: " + reason
        )


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def block_network(*args, **kwargs):
    raise AssertionError("NETWORK ACCESS BLOCKED")


def main():
    print(
        "\n=== JAHVASCRIPT WEBULL LOGGING ISOLATION AUDIT ==="
    )

    protected = verify_sources()
    require(SCHEDULE.is_file(), "Collection schedule missing")
    schedule_hash = sha(SCHEDULE)

    version = importlib.metadata.version(
        "webull-openapi-python-sdk"
    )
    require(version == "3.0.3", "SDK version changed")

    from webull.core.client import ApiClient
    from webull.data.data_client import DataClient
    from webull.data import data_client as data_module

    sdk_sources = {
        "ApiClient": Path(
            ApiClient.__init__.__code__.co_filename
        ),
        "DataClient": Path(data_module.__file__),
    }

    sdk_hashes = {
        name: sha(path)
        for name, path in sdk_sources.items()
    }

    print("SDK version:", version)
    print("Frozen research fingerprints: VERIFIED")

    original_cwd = Path.cwd()

    with tempfile.TemporaryDirectory(
        prefix="jahvascript_webull_logging_"
    ) as temporary:

        workspace = Path(temporary).resolve()
        token_dir = workspace / "tokens"
        token_dir.mkdir(mode=0o700)

        events = []
        proposed_files = []

        def fake_stream_logger(self, *args, **kwargs):
            events.append("STREAM_LOGGER")
            return None

        def fake_file_logger(self, *args, **kwargs):
            require(
                not args,
                "Unexpected positional file logger arguments"
            )

            path_value = kwargs.get("path")
            require(
                isinstance(path_value, (str, os.PathLike)),
                "Missing file logger path"
            )

            candidate = Path(path_value)

            if not candidate.is_absolute():
                candidate = Path.cwd() / candidate

            resolved = candidate.resolve(strict=False)

            require(
                resolved.is_relative_to(workspace),
                "SDK default log path escapes test workspace"
            )

            require(
                resolved != workspace
                and resolved != token_dir
                and not resolved.is_relative_to(token_dir),
                "Logs must not share token storage"
            )

            require(
                not candidate.is_symlink(),
                "Symlinked log file forbidden"
            )

            proposed_files.append(resolved)
            events.append("FILE_LOGGER")

            # Intentionally do not open or create a file.
            return None

        with patch.object(
            requests.sessions.Session,
            "send",
            block_network
        ), patch.object(
            socket.socket,
            "connect",
            block_network
        ), patch.object(
            socket.socket,
            "connect_ex",
            block_network
        ), patch.object(
            ApiClient,
            "set_stream_logger",
            fake_stream_logger
        ), patch.object(
            ApiClient,
            "set_file_logger",
            fake_file_logger
        ), patch.object(
            data_module.ClientInitializer,
            "initializer",
            return_value=None
        ), patch.dict(
            os.environ,
            {
                "WEBULL_OPENAPI_TOKEN_DIR": str(token_dir),
            }
        ):
            try:
                os.chdir(workspace)

                api = ApiClient(
                    "JAHVASCRIPT_FAKE_KEY",
                    "JAHVASCRIPT_FAKE_SECRET",
                    "us"
                )

                api.set_token_dir(str(token_dir))

                require(
                    Path(api.get_token_dir()).resolve()
                    == token_dir.resolve(),
                    "Token storage path mismatch"
                )

                data = DataClient(api)

                require(
                    data is not None,
                    "DataClient initialization failed"
                )

                require(
                    events.count("STREAM_LOGGER") == 1,
                    "Unexpected stream logger initialization"
                )

                require(
                    events.count("FILE_LOGGER") == 1,
                    "Unexpected file logger initialization"
                )

                require(
                    len(proposed_files) == 1,
                    "Unexpected number of log destinations"
                )

                require(
                    not proposed_files[0].exists(),
                    "Logger created a file unexpectedly"
                )

                require(
                    not list(token_dir.iterdir()),
                    "Token directory was modified"
                )

                print("\n--- SDK LOGGER INITIALIZATION ---")
                print("Synthetic ApiClient: PASS")
                print("DataClient initialization: PASS")
                print("SDK token initialization: MOCKED")
                print("Stream logger call intercepted: PASS")
                print("File logger call intercepted: PASS")
                print("Proposed log destination isolated: PASS")
                print(
                    "Log destination relative to workspace:",
                    proposed_files[0].relative_to(workspace)
                )
                print("Log files created: NONE")
                print("Token files created: NONE")
                print("Existing real logger files accessed: NONE")

            finally:
                os.chdir(original_cwd)

    require(
        verify_sources() == protected,
        "Protected research changed"
    )
    require(
        sha(SCHEDULE) == schedule_hash,
        "Collection schedule changed"
    )

    for name, path in sdk_sources.items():
        require(
            sha(path) == sdk_hashes[name],
            name + " SDK source changed"
        )

    report = {
        "version": "WEBULL_LOGGING_ISOLATION_AUDIT_V001",
        "sdk_version": version,
        "collection_schedule_sha256": schedule_hash,
        "research_fingerprints": protected,
        "sdk_source_sha256": sdk_hashes,
        "data_client_init": "PASS",
        "stream_logger_intercept": "PASS",
        "file_logger_intercept": "PASS",
        "proposed_log_path_isolated": True,
        "log_files_created": 0,
        "token_files_created": 0,
        "actual_file_logger_executed": False,
        "real_credentials_accessed": False,
        "real_network_requests": 0,
        "live_webull_schema_verified": False,
        "real_sessions_admitted": 0,
        "trade_authorization": False
    }

    FORWARD.mkdir(parents=True, exist_ok=True)

    if REPORT.exists():
        require(
            REPORT.is_file()
            and not REPORT.is_symlink()
            and json.loads(REPORT.read_text()) == report,
            "Existing audit differs; original preserved"
        )
        print("\nExisting logging audit: VERIFIED")
    else:
        os.umask(0o077)

        with REPORT.open("x") as handle:
            json.dump(report, handle, indent=2)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())

        REPORT.chmod(0o600)
        print("\nNew logging audit: SAVED")

    print("\n--- FINAL RESULT ---")
    print("DataClient logging path: OFFLINE VERIFIED")
    print("Existing logger files: UNCHANGED")
    print("Existing token files: NOT ACCESSED")
    print("Actual file logging: NOT EXECUTED")
    print("Live Webull response schema: NOT VERIFIED")
    print("Network requests: NONE")
    print("Research V001: UNCHANGED")
    print("Real sessions admitted: 0")
    print("Order execution: DISABLED")
    print("\nSTEP 15H.65X LOGGING ISOLATION VERIFIED")


if __name__ == "__main__":
    main()
