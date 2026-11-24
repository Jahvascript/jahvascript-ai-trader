"""Offline Webull SDK bootstrap-to-HTTP integration audit.

No credentials, real HTTP requests, token operations, or trading.
"""

import hashlib
import json
import os
import socket
from pathlib import Path
from unittest.mock import patch

import requests

from forward_collection_schedule_v001 import verify_sources
from webull_probe_request_guard_v001 import load_request_classes
from webull_probe_bootstrap_guard_v001 import (
    BootstrapGuard,
    BOOTSTRAP_PATHS,
)
from webull_probe_destination_guard_v001 import HOST

ROOT = Path(__file__).resolve().parent
FORWARD = ROOT / "journal/forward_evaluation_v001"
SCHEDULE = FORWARD / "collection_schedule_v001.json"
REPORT = FORWARD / "webull_bootstrap_transport_audit_v001.json"

EVENTS = []
FIXTURE = {
    "status": 200,
    "content": b'{"synthetic":true}',
}


def require(condition, message):
    if not condition:
        raise AssertionError(
            "BOOTSTRAP TRANSPORT AUDIT FAILED: " + message
        )


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def block_network(*args, **kwargs):
    raise AssertionError("REAL NETWORK CONNECTION BLOCKED")


class GuardedSession(requests.Session):
    """Check final HTTP request before any transport call."""

    bootstrap_guard = None

    def send(self, prepared, **kwargs):
        require(
            self.bootstrap_guard is not None,
            "Bootstrap guard missing"
        )

        self.bootstrap_guard.check_prepared(prepared)

        kwargs["allow_redirects"] = False

        response = super().send(prepared, **kwargs)

        self.bootstrap_guard.check_result(
            response, prepared.url
        )

        return response


def fake_send(self, prepared, **kwargs):
    """Intercept the actual HTTP send. Never use a socket."""

    require(
        isinstance(self, GuardedSession),
        "Unexpected session type"
    )
    require(
        kwargs.get("allow_redirects") is False,
        "Redirect following was enabled"
    )

    EVENTS.append((
        prepared.method,
        prepared.url
    ))

    response = requests.Response()
    response.status_code = FIXTURE["status"]
    response._content = FIXTURE["content"]
    response.headers["Content-Type"] = "application/json"
    response.url = prepared.url
    response.request = prepared

    return response


class OfflineBootstrapApi:
    """Allow SDK bootstrap requests, then guard their HTTP form."""

    def __init__(self, classes, host=HOST, override_path=None):
        self.guard = BootstrapGuard(classes)
        self.host = host
        self.override_path = override_path

    def get_response(self, request):
        # Validate SDK request identity, fields, and budget.
        self.guard.check_sdk(request)

        from webull.core.http.response import Response

        protocol = Response.__init__.__globals__["PT"].HTTPS

        path = self.override_path or request.get_url()

        # Empty POST payloads are synthetic fixtures.
        # Real token payload formats are NOT verified here.
        content = (
            None if request.get_method() == "GET"
            else b"{}"
        )

        sdk_response = Response(
            host=self.host,
            url=path,
            method=request.get_method(),
            headers={
                "Accept": "application/json",
                "Content-Type": "application/json",
            },
            protocol=protocol,
            content=content,
            port=443,
            read_timeout=5,
            connect_timeout=5,
            verify=True,
        )

        GuardedSession.bootstrap_guard = self.guard

        status, headers, raw, response = (
            sdk_response.get_response_object()
        )

        require(
            response.status_code == status,
            "HTTP response status changed"
        )
        require(
            response.content == raw,
            "HTTP response bytes changed"
        )

        return response


def expect_block(label, callback):
    before = len(EVENTS)

    try:
        callback()
    except PermissionError:
        require(
            len(EVENTS) == before,
            "Blocked request reached transport"
        )
        print(label + ": REJECTED")
        return

    raise AssertionError(
        "UNSAFE OPERATION ACCEPTED: " + label
    )


def main():
    print(
        "\n=== JAHVASCRIPT WEBULL BOOTSTRAP TRANSPORT AUDIT ==="
    )

    before_sources = verify_sources()

    require(SCHEDULE.is_file(), "Collection schedule missing")
    schedule_hash = sha(SCHEDULE)

    classes = load_request_classes()

    print("Protected research fingerprints: VERIFIED")
    print("Installed SDK request classes: VERIFIED")

    from webull.core.http import response as transport

    with patch.object(
        transport, "Session", GuardedSession
    ), patch.object(
        requests.sessions.Session, "send", fake_send
    ), patch.object(
        socket.socket, "connect", block_network
    ), patch.object(
        socket.socket, "connect_ex", block_network
    ):

        print("\n--- GUARDED SDK BOOTSTRAP FIXTURES ---")

        api = OfflineBootstrapApi(classes)

        for name, (path, method) in BOOTSTRAP_PATHS.items():
            response = api.get_response(classes[name]())

            require(
                response.status_code == 200,
                "Synthetic HTTP 200 failed"
            )
            require(
                response.json().get("synthetic") is True,
                "Synthetic JSON decode failed"
            )

            require(
                EVENTS[-1] == (
                    method,
                    "https://" + HOST + path
                ),
                "Unexpected HTTP operation"
            )

            print("PASS:", name, method, path)

        require(
            len(EVENTS) == 4,
            "Expected exactly four fixture sends"
        )

        print("\n--- SAFETY REJECTION TESTS ---")

        expect_block(
            "Historical request in bootstrap-only client",
            lambda: OfflineBootstrapApi(classes).get_response(
                classes["BatchHistoricalBarsRequest"]()
            )
        )

        expect_block(
            "Unauthorized destination",
            lambda: OfflineBootstrapApi(
                classes, host="example.com"
            ).get_response(classes["GetConfigRequest"]())
        )

        expect_block(
            "Unauthorized API path",
            lambda: OfflineBootstrapApi(
                classes,
                override_path="/trade/order/place"
            ).get_response(classes["GetConfigRequest"]())
        )

        limited = OfflineBootstrapApi(classes)

        limited.get_response(classes["GetConfigRequest"]())
        limited.get_response(classes["GetConfigRequest"]())

        expect_block(
            "Third configuration request",
            lambda: limited.get_response(
                classes["GetConfigRequest"]()
            )
        )

        FIXTURE["status"] = 302
        FIXTURE["content"] = b""

        try:
            OfflineBootstrapApi(classes).get_response(
                classes["GetConfigRequest"]()
            )
        except PermissionError:
            print("HTTP redirect: REJECTED")
        else:
            raise AssertionError(
                "Redirect unexpectedly accepted"
            )

        FIXTURE["status"] = 503
        FIXTURE["content"] = b'{"error":"synthetic"}'

        error_response = OfflineBootstrapApi(
            classes
        ).get_response(classes["GetConfigRequest"]())

        require(
            error_response.status_code == 503
            and error_response.content == FIXTURE["content"],
            "HTTP 503 response changed"
        )

        print("HTTP 503 status and bytes: PRESERVED")
        print("Actual network connections: BLOCKED")

    require(
        verify_sources() == before_sources,
        "Protected research changed"
    )
    require(
        sha(SCHEDULE) == schedule_hash,
        "Collection schedule changed"
    )

    report = {
        "version": "WEBULL_BOOTSTRAP_TRANSPORT_AUDIT_V001",
        "scope": "OFFLINE_SYNTHETIC_ONLY",
        "collection_schedule_sha256": schedule_hash,
        "research_fingerprints": before_sources,
        "bootstrap_sdk_to_http": "PASS",
        "four_bootstrap_routes": "PASS",
        "unauthorized_sdk_class": "REJECTED",
        "unauthorized_host": "REJECTED",
        "unauthorized_path": "REJECTED",
        "request_budget": "PASS",
        "redirect": "REJECTED",
        "http_503": "PRESERVED",
        "real_network_requests": 0,
        "actual_token_initialization_executed": False,
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
        print("\nExisting audit: VERIFIED")
    else:
        os.umask(0o077)

        with REPORT.open("x") as handle:
            json.dump(report, handle, indent=2)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())

        REPORT.chmod(0o600)
        print("\nNew bootstrap transport audit: SAVED")

    print("\n--- FINAL RESULT ---")
    print("SDK-to-HTTP bootstrap integration: OFFLINE VERIFIED")
    print("Initialization request destinations: VERIFIED")
    print("Request budgets and redirects: VERIFIED")
    print("Actual token initialization: NOT EXECUTED")
    print("Actual token payloads: NOT VERIFIED")
    print("Live Webull response schema: NOT VERIFIED")
    print("Network requests: NONE")
    print("Credentials accessed: NONE")
    print("Research V001: UNCHANGED")
    print("Real sessions admitted: 0")
    print("Order execution: DISABLED")
    print("\nSTEP 15H.65Q BOOTSTRAP TRANSPORT VERIFIED")


if __name__ == "__main__":
    main()
