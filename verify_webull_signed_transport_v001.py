"""Offline integration of real Webull signing and guarded HTTP transport.

Synthetic keys only. No DataClient, token manager, network, or trading.
"""

import hashlib
import importlib.metadata
import json
import os
import socket
from pathlib import Path
from unittest.mock import patch

import requests
from webull.core.exception.exceptions import ClientException

from forward_collection_schedule_v001 import verify_sources
from webull_probe_request_guard_v001 import (
    load_request_classes,
    ProbeRequestGuard,
)
from webull_probe_destination_guard_v001 import (
    HOST,
    MARKET_PATH,
    check_prepared_request,
    check_response,
)
from verify_webull_probe_parameters_v001 import (
    make_request,
    validate_request,
)

ROOT = Path(__file__).resolve().parent
FORWARD = ROOT / "journal/forward_evaluation_v001"
SCHEDULE = FORWARD / "collection_schedule_v001.json"
REPORT = FORWARD / "webull_signed_transport_audit_v001.json"

EVENTS = []
BLOCKED = []
FIXTURE_BYTES = b'{"synthetic":true,"bars":[]}'


def require(condition, reason):
    if not condition:
        raise AssertionError(
            "SIGNED TRANSPORT AUDIT FAILED: " + reason
        )


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def deny_socket(*args, **kwargs):
    raise AssertionError(
        "REAL NETWORK CONNECTION ATTEMPTED"
    )


class GuardedSession(requests.Session):
    """Validate final signed HTTP request before transport."""

    def send(self, prepared, **kwargs):
        try:
            path = check_prepared_request(prepared)

            require(
                path == MARKET_PATH,
                "Only historical market data permitted"
            )
        except (PermissionError, ValueError):
            BLOCKED.append("DESTINATION_OR_BODY")
            raise

        kwargs["allow_redirects"] = False

        response = super().send(prepared, **kwargs)

        check_response(response, prepared.url)
        return response


def synthetic_send(self, prepared, **kwargs):
    """Final fake transport. It cannot contact a real server."""

    require(
        isinstance(self, GuardedSession),
        "Unguarded HTTP session attempted to send"
    )

    require(
        kwargs.get("allow_redirects") is False,
        "Redirect following was not disabled"
    )

    require(
        check_prepared_request(prepared) == MARKET_PATH,
        "Unapproved final HTTP request"
    )

    EVENTS.append("SYNTHETIC_HTTP_SEND")

    response = requests.Response()
    response.status_code = 200
    response._content = FIXTURE_BYTES
    response.headers["Content-Type"] = "application/json"
    response.url = prepared.url
    response.request = prepared
    response.history = []

    return response


def new_api(host=HOST):
    """Create real SDK ApiClient with fictional credentials."""

    from webull.core.client import ApiClient

    api = ApiClient(
        "JAHVASCRIPT_OFFLINE_TEST_KEY",
        "JAHVASCRIPT_OFFLINE_TEST_SECRET",
        "us",
    )

    api.add_endpoint("us", host)
    return api


def checked_request(api, classes, guard, symbol="SPY"):
    cls = classes["BatchHistoricalBarsRequest"]

    request = make_request(cls)

    if symbol != "SPY":
        request.set_symbols([symbol])

    # First boundary: SDK request fields.
    validate_request(request, cls)

    # Second boundary: exact SDK class and request budget.
    require(
        guard.permit(request) == "HISTORICAL_MARKET_DATA",
        "SDK request class rejected"
    )

    # Third boundary: real SDK signing, endpoint resolution,
    # and HTTP construction. GuardedSession checks final HTTP.
    return api.get_response(request)


def expect_block(label, callback):
    before_sends = len(EVENTS)
    before_blocks = len(BLOCKED)

    try:
        callback()
    except PermissionError:
        pass
    except ClientException as exc:
        require(
            len(BLOCKED) > before_blocks
            and 'WEBULL DESTINATION BLOCKED' in str(exc),
            'Unexpected SDK exception: ' + label
        )

    require(
        len(EVENTS) == before_sends,
        "Unsafe request reached synthetic transport: " + label
    )

    require(
        len(BLOCKED) > before_blocks or
        label in (
            "Second historical request",
            "Unauthorized symbol",
        ),
        "No rejection evidence: " + label
    )

    print(label + ": REJECTED")


def main():
    print(
        "\n=== JAHVASCRIPT SIGNED WEBULL TRANSPORT AUDIT ==="
    )

    protected = verify_sources()

    require(SCHEDULE.is_file(), "Collection schedule missing")

    schedule_hash = sha(SCHEDULE)

    sdk_version = importlib.metadata.version(
        "webull-openapi-python-sdk"
    )

    require(sdk_version == "3.0.3", "SDK version changed")

    classes = load_request_classes()

    import webull.core.http.response as transport

    sdk_response_file = Path(transport.__file__)
    sdk_response_hash = sha(sdk_response_file)

    print("Webull SDK:", sdk_version)
    print("Frozen research fingerprints: VERIFIED")
    print("Installed request classes: VERIFIED")

    # Both layers of HTTP delivery are intercepted.
    # Socket connections are independently blocked.
    with patch.object(
        transport, "Session", GuardedSession
    ), patch.object(
        requests.sessions.Session, "send", synthetic_send
    ), patch.object(
        socket.socket, "connect", deny_socket
    ), patch.object(
        socket.socket, "connect_ex", deny_socket
    ), patch.dict(
        os.environ,
        {
            "HTTP_DEBUG": "false",
            "WEBULL_HTTP_DEBUG": "false",
        }
    ):
        print("\n--- REAL SDK, SYNTHETIC HTTP ---")

        api = new_api()
        guard = ProbeRequestGuard(classes)

        response = checked_request(api, classes, guard)

        require(
            response is not None
            and response.status_code == 200,
            "SDK did not return synthetic HTTP 200"
        )

        require(
            response.content == FIXTURE_BYTES,
            "Synthetic response bytes changed"
        )

        require(
            response.json().get("synthetic") is True,
            "Synthetic JSON decoding failed"
        )

        require(
            len(EVENTS) == 1,
            "Expected one synthetic HTTP send"
        )

        print("ApiClient SDK signing path: PASS")
        print("SDK endpoint resolution: PASS")
        print("Signed prepared HTTPS validation: PASS")
        print("One synthetic HTTP response: PASS")
        print("Response bytes preserved: PASS")
        print("Synthetic JSON decoding: PASS")

        print("\n--- FAIL-CLOSED REJECTIONS ---")

        expect_block(
            "Second historical request",
            lambda: checked_request(api, classes, guard)
        )

        expect_block(
            "Unauthorized symbol",
            lambda: checked_request(
                new_api(),
                classes,
                ProbeRequestGuard(classes),
                symbol="QQQ",
            )
        )

        expect_block(
            "Missing sandbox override",
            lambda: checked_request(
                # Default SDK host is not sandbox.
                __import__(
                    "webull.core.client",
                    fromlist=["ApiClient"],
                ).ApiClient(
                    "JAHVASCRIPT_OFFLINE_TEST_KEY",
                    "JAHVASCRIPT_OFFLINE_TEST_SECRET",
                    "us",
                ),
                classes,
                ProbeRequestGuard(classes),
            )
        )

        expect_block(
            "Unauthorized destination",
            lambda: checked_request(
                new_api(host="example.com"),
                classes,
                ProbeRequestGuard(classes),
            )
        )

        print("Unrestricted HTTP sends: NONE")
        print("Socket connections: BLOCKED")

    require(
        verify_sources() == protected,
        "Frozen research fingerprints changed"
    )

    require(
        sha(SCHEDULE) == schedule_hash,
        "Collection schedule changed"
    )

    require(
        sha(sdk_response_file) == sdk_response_hash,
        "Webull SDK source changed"
    )

    report = {
        "version": "WEBULL_SIGNED_TRANSPORT_AUDIT_V001",
        "scope": "OFFLINE_SYNTHETIC_ONLY",
        "sdk_version": sdk_version,
        "sdk_response_sha256": sdk_response_hash,
        "collection_schedule_sha256": schedule_hash,
        "research_fingerprints": protected,
        "signed_sdk_http_path": "PASS",
        "final_prepared_request_guard": "PASS",
        "synthetic_http_200": "PASS",
        "response_bytes": "PRESERVED",
        "second_request": "REJECTED",
        "unauthorized_symbol": "REJECTED",
        "missing_sandbox_override": "REJECTED",
        "unauthorized_destination": "REJECTED",
        "real_network_requests": 0,
        "real_credentials_accessed": False,
        "token_manager_executed": False,
        "data_client_instantiated": False,
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
            "Existing audit differs; original preserved"
        )
        print("\nExisting signed transport audit: VERIFIED")
    else:
        os.umask(0o077)

        with REPORT.open("x") as handle:
            json.dump(report, handle, indent=2)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())

        REPORT.chmod(0o600)
        print("\nNew signed transport audit: SAVED")

    print("\n--- FINAL RESULT ---")
    print("Real SDK signing and routing: TESTED OFFLINE")
    print("Guarded final HTTP request: VERIFIED")
    print("Real Webull authentication: NOT EXECUTED")
    print("Live Webull candle schema: NOT VERIFIED")
    print("Real network requests: NONE")
    print("Research V001: UNCHANGED")
    print("Real sessions admitted: 0")
    print("Order execution: DISABLED")
    print("\nSTEP 15H.65AB SIGNED TRANSPORT VERIFIED")


if __name__ == "__main__":
    main()
