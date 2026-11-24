"""Test Webull token payload rules using fictional tokens only."""

import json
import os
import socket
from pathlib import Path
from unittest.mock import patch

import requests

from forward_collection_schedule_v001 import verify_sources
from webull_probe_request_guard_v001 import load_request_classes
from webull_probe_token_guard_v001 import (
    TokenPayloadGuard,
    ROUTES,
)
from webull_probe_destination_guard_v001 import HOST

ROOT = Path(__file__).resolve().parent
REPORT = (
    ROOT / "journal/forward_evaluation_v001/"
    "webull_token_payload_guard_audit_v001.json"
)

FAKE_TOKEN = "JAHVASCRIPT_SYNTHETIC_TOKEN_ONLY"


def deny_network(*args, **kwargs):
    raise AssertionError("NETWORK CONNECTION BLOCKED")


def require(ok, message):
    if not ok:
        raise AssertionError(
            "TOKEN GUARD AUDIT FAILED: " + message
        )


def make_sdk(classes, name):
    request = classes[name]()

    if name != "GetConfigRequest":
        request.set_token(FAKE_TOKEN)

    return request


def make_http(name, body=None):
    path, method = ROUTES[name]

    if method == "GET":
        return requests.Request(
            "GET", "https://" + HOST + path
        ).prepare()

    return requests.Request(
        "POST",
        "https://" + HOST + path,
        json={"token": FAKE_TOKEN}
        if body is None else body
    ).prepare()


def rejected(label, action):
    try:
        action()
    except PermissionError:
        print(label + ": REJECTED")
        return

    raise AssertionError(
        "UNSAFE PAYLOAD ACCEPTED: " + label
    )


def main():
    print(
        "\n=== JAHVASCRIPT WEBULL TOKEN PAYLOAD GUARD AUDIT ==="
    )

    before = verify_sources()
    classes = load_request_classes()

    with patch.object(
        requests.sessions.Session, "send", deny_network
    ), patch.object(
        socket.socket, "connect", deny_network
    ), patch.object(
        socket.socket, "connect_ex", deny_network
    ):
        print("\n--- APPROVED SYNTHETIC SDK REQUESTS ---")

        guard = TokenPayloadGuard(classes)

        for name in ROUTES:
            sdk_request = make_sdk(classes, name)
            http_request = make_http(name)

            require(
                guard.check_sdk(sdk_request) == name,
                "SDK request rejected"
            )
            require(
                guard.check_prepared(
                    http_request, sdk_request
                ) == name,
                "HTTP request rejected"
            )

            print(name + ": PASS")

        print("\n--- SAFETY REJECTION TESTS ---")

        name = "CheckTokenRequest"
        good_sdk = make_sdk(classes, name)

        rejected(
            "Missing token field",
            lambda: TokenPayloadGuard(
                classes
            ).check_sdk(classes[name]())
        )

        for label, value in (
            ("Empty token", ""),
            ("Blank token", "   "),
            ("Numeric token", 123),
            ("Oversized token", "x" * 4097),
        ):
            request = classes[name]()
            request.set_token(value)

            rejected(
                label,
                lambda r=request: TokenPayloadGuard(
                    classes
                ).check_sdk(r)
            )

        extra = make_sdk(classes, name)
        extra.add_body_params("extra", "bad")

        rejected(
            "Extra SDK body field",
            lambda: TokenPayloadGuard(
                classes
            ).check_sdk(extra)
        )

        wrong = make_http(
            name,
            body={"token": "DIFFERENT_SYNTHETIC_TOKEN"}
        )

        rejected(
            "Changed HTTP token payload",
            lambda: TokenPayloadGuard(
                classes
            ).check_prepared(wrong, good_sdk)
        )

        duplicate = make_http(name)
        duplicate.body = (
            b'{"token":"x","token":"y"}'
        )

        rejected(
            "Duplicate JSON token keys",
            lambda: TokenPayloadGuard(
                classes
            ).check_prepared(duplicate, good_sdk)
        )

        malformed = make_http(name)
        malformed.body = b'{"token":'

        rejected(
            "Malformed token JSON",
            lambda: TokenPayloadGuard(
                classes
            ).check_prepared(malformed, good_sdk)
        )

        bad_type = make_http(name)
        bad_type.headers["Content-Type"] = "text/plain"

        rejected(
            "Incorrect Content-Type",
            lambda: TokenPayloadGuard(
                classes
            ).check_prepared(bad_type, good_sdk)
        )

        bad_host = make_http(name)
        bad_host.url = (
            "https://example.com/auth/tokens/check"
        )

        rejected(
            "Unauthorized destination",
            lambda: TokenPayloadGuard(
                classes
            ).check_prepared(bad_host, good_sdk)
        )

        wrong_class = classes[
            "BatchHistoricalBarsRequest"
        ]()

        rejected(
            "Market-data request as bootstrap",
            lambda: TokenPayloadGuard(
                classes
            ).check_sdk(wrong_class)
        )

        limited = TokenPayloadGuard(classes)
        limited.check_sdk(make_sdk(classes, name))
        limited.check_sdk(make_sdk(classes, name))

        rejected(
            "Excess bootstrap requests",
            lambda: limited.check_sdk(
                make_sdk(classes, name)
            )
        )

        print("Live network transport: BLOCKED")

    require(
        verify_sources() == before,
        "Protected research changed"
    )

    report = {
        "version": "WEBULL_TOKEN_PAYLOAD_GUARD_V001",
        "scope": "OFFLINE_SYNTHETIC_ONLY",
        "research_fingerprints": before,
        "sdk_request_shapes": "PASS",
        "prepared_http_shapes": "PASS",
        "invalid_payload_tests": 12,
        "invalid_payloads": "REJECTED",
        "token_values_recorded": False,
        "real_credentials_accessed": False,
        "real_network_requests": 0,
        "live_authentication_verified": False,
        "live_webull_schema_verified": False,
        "real_sessions_admitted": 0,
        "trade_authorization": False,
    }

    REPORT.parent.mkdir(parents=True, exist_ok=True)

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
        print("\nNew token guard audit: SAVED")

    print("\n--- FINAL RESULT ---")
    print("Token request body rules: OFFLINE VERIFIED")
    print("SDK-to-HTTP payload consistency: VERIFIED")
    print("Synthetic malformed payloads: REJECTED")
    print("Actual Webull authentication: NOT EXECUTED")
    print("Live transport integration: NOT YET VERIFIED")
    print("Live Webull candle schema: NOT VERIFIED")
    print("Credential values printed: NONE")
    print("Network requests: NONE")
    print("Research V001: UNCHANGED")
    print("Real sessions admitted: 0")
    print("Order execution: DISABLED")
    print("\nSTEP 15H.65T TOKEN GUARD VERIFIED")


if __name__ == "__main__":
    main()
