"""Offline SDK bootstrap and destination guard verification."""

import hashlib
import json
import os
import socket
from pathlib import Path
from unittest.mock import patch

import requests

from forward_collection_schedule_v001 import verify_sources
from webull_probe_request_guard_v001 import (
    load_request_classes,
)
from webull_probe_bootstrap_guard_v001 import (
    BootstrapGuard,
    BOOTSTRAP_PATHS,
)
from webull_probe_destination_guard_v001 import HOST

ROOT = Path(__file__).resolve().parent
FORWARD = ROOT / "journal/forward_evaluation_v001"
REPORT = FORWARD / "webull_bootstrap_guard_audit_v001.json"


def require(ok, message):
    if not ok:
        raise AssertionError(
            "BOOTSTRAP AUDIT FAILED: " + message
        )


def block_network(*args, **kwargs):
    raise AssertionError("REAL NETWORK ACCESS BLOCKED")


def prepared(path, method, host=HOST, body=None):
    url = "https://" + host + path

    if method == "GET":
        return requests.Request(
            "GET", url, data=body
        ).prepare()

    if body is None:
        return requests.Request(
            "POST", url, json={}
        ).prepare()

    return requests.Request(
        "POST", url, data=body
    ).prepare()


def expect_rejection(label, action):
    try:
        action()
    except PermissionError:
        print(label + ": REJECTED")
        return

    raise AssertionError(
        "UNSAFE REQUEST ACCEPTED: " + label
    )


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main():
    print(
        "\n=== JAHVASCRIPT WEBULL BOOTSTRAP GUARD AUDIT ==="
    )

    before = verify_sources()

    classes = load_request_classes()

    print("Installed Webull SDK classes: VERIFIED")
    print("Frozen research fingerprints: VERIFIED")

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
    ):
        guard = BootstrapGuard(classes)

        print("\n--- VERIFIED INITIALIZATION OPERATIONS ---")

        for name, (path, method) in BOOTSTRAP_PATHS.items():
            request = classes[name]()

            require(
                guard.check_sdk(request) == name,
                "SDK bootstrap request rejected"
            )

            http = prepared(path, method)

            require(
                guard.check_prepared(http) == path,
                "Prepared HTTPS request rejected"
            )

            print(
                "PASS:", method, path,
                "|", name
            )

        print("\n--- SAFETY REJECTION TESTS ---")

        expect_rejection(
            "Unknown SDK request class",
            lambda: BootstrapGuard(
                classes
            ).check_sdk(object())
        )

        expect_rejection(
            "Market-data request in bootstrap path",
            lambda: BootstrapGuard(
                classes
            ).check_sdk(
                classes["BatchHistoricalBarsRequest"]()
            )
        )

        spoof = type(
            "GetConfigRequest",
            (),
            {}
        )

        expect_rejection(
            "Forged SDK bootstrap class",
            lambda: BootstrapGuard(
                classes
            ).check_sdk(spoof())
        )

        expect_rejection(
            "Unauthorized hostname",
            lambda: guard.check_prepared(
                prepared(
                    "/openapi/config",
                    "GET",
                    host="example.com"
                )
            )
        )

        expect_rejection(
            "Production hostname",
            lambda: guard.check_prepared(
                prepared(
                    "/openapi/config",
                    "GET",
                    host="api.webull.com"
                )
            )
        )

        expect_rejection(
            "Trade order path",
            lambda: guard.check_prepared(
                prepared(
                    "/trade/order/place",
                    "POST"
                )
            )
        )

        expect_rejection(
            "Bootstrap URL query",
            lambda: guard.check_prepared(
                prepared(
                    "/openapi/config?extra=1",
                    "GET"
                )
            )
        )

        expect_rejection(
            "Unexpected bootstrap body",
            lambda: guard.check_prepared(
                prepared(
                    "/auth/tokens/create",
                    "POST",
                    body=b'{"unexpected":"value"}'
                )
            )
        )

        expect_rejection(
            "Incorrect bootstrap method",
            lambda: guard.check_prepared(
                prepared(
                    "/openapi/config",
                    "POST"
                )
            )
        )

        repeated = BootstrapGuard(classes)

        repeated.check_sdk(
            classes["GetConfigRequest"]()
        )
        repeated.check_sdk(
            classes["GetConfigRequest"]()
        )

        expect_rejection(
            "Excess configuration requests",
            lambda: repeated.check_sdk(
                classes["GetConfigRequest"]()
            )
        )

        response = requests.Response()
        response.url = (
            "https://" + HOST + "/openapi/config"
        )
        response.history = []
        response.status_code = 200

        require(
            guard.check_result(
                response, response.url
            ),
            "Valid synthetic response rejected"
        )

        print("Synthetic HTTP 200 destination: PASS")

        response.status_code = 302

        expect_rejection(
            "Bootstrap HTTP redirect",
            lambda: guard.check_result(
                response, response.url
            )
        )

        print("Real network transport: BLOCKED")

    require(
        verify_sources() == before,
        "Protected research fingerprints changed"
    )

    report = {
        "version": "WEBULL_BOOTSTRAP_GUARD_AUDIT_V001",
        "scope": "OFFLINE_SYNTHETIC_ONLY",
        "research_fingerprints": before,
        "bootstrap_paths": {
            name: {
                "path": path,
                "method": method,
            }
            for name, (path, method)
            in sorted(BOOTSTRAP_PATHS.items())
        },
        "sdk_class_validation": "PASS",
        "bootstrap_parameter_validation": "PASS",
        "prepared_destination_validation": "PASS",
        "request_budget": "PASS",
        "redirect_rejection": "PASS",
        "real_network_requests": 0,
        "sdk_token_initialization_executed": False,
        "live_authentication_verified": False,
        "live_webull_schema_verified": False,
        "real_sessions_admitted": 0,
        "trade_authorization": False,
    }

    FORWARD.mkdir(parents=True, exist_ok=True)

    if REPORT.exists():
        require(
            REPORT.is_file()
            and not REPORT.is_symlink()
            and json.loads(REPORT.read_text()) == report,
            "Existing audit differs; not overwritten"
        )
        print("\nExisting audit: VERIFIED")
    else:
        os.umask(0o077)
        with REPORT.open("x") as handle:
            json.dump(report, handle, indent=2)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())

        REPORT.chmod(0o600)
        print("\nNew bootstrap audit: SAVED")

    print("\n--- FINAL RESULT ---")
    print("Bootstrap SDK requests: OFFLINE VERIFIED")
    print("Bootstrap destination guard: OFFLINE VERIFIED")
    print("Actual SDK authentication: NOT EXECUTED")
    print("Live network enforcement: NOT YET INTEGRATED")
    print("Live Webull candle schema: NOT VERIFIED")
    print("Webull network requests: NONE")
    print("Credentials accessed: NONE")
    print("Research V001: UNCHANGED")
    print("Real sessions admitted: 0")
    print("Order execution: DISABLED")

    print("\nSTEP 15H.65P BOOTSTRAP GUARD VERIFIED")


if __name__ == "__main__":
    main()
