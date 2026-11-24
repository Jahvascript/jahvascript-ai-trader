"""Offline integration of Webull token guards and SDK HTTP transport.

Uses fictional tokens and intercepted HTTP responses only.
No real authentication, network access, token files, or trading.
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
from webull_probe_token_guard_v001 import (
    TokenPayloadGuard,
    ROUTES,
)
from webull_probe_destination_guard_v001 import HOST

ROOT = Path(__file__).resolve().parent
FORWARD = ROOT / "journal/forward_evaluation_v001"
REPORT = FORWARD / "webull_token_transport_audit_v001.json"
SCHEDULE = FORWARD / "collection_schedule_v001.json"

FAKE_TOKEN = "JAHVASCRIPT_SYNTHETIC_TOKEN_ONLY"
EVENTS = []
FIXTURE = {
    "status": 200,
    "body": b'{"synthetic":true}',
}


def require(ok, reason):
    if not ok:
        raise AssertionError("TOKEN TRANSPORT AUDIT FAILED: " + reason)


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def block_network(*args, **kwargs):
    raise AssertionError("REAL NETWORK CONNECTION BLOCKED")


class GuardedSession(requests.Session):
    context = None

    def send(self, prepared, **kwargs):
        require(
            self.context is not None,
            "Missing token-guard context"
        )

        guard, sdk_request = self.context

        # Validate the actual final HTTP request, including
        # its JSON token field, before any transport call.
        guard.check_prepared(prepared, sdk_request)

        kwargs["allow_redirects"] = False

        response = super().send(prepared, **kwargs)

        guard.check_result(response, prepared.url)

        return response


def fake_send(self, prepared, **kwargs):
    require(
        isinstance(self, GuardedSession),
        "Unexpected transport"
    )
    require(
        kwargs.get("allow_redirects") is False,
        "Redirect following was enabled"
    )

    EVENTS.append((prepared.method, prepared.url))

    response = requests.Response()
    response.status_code = FIXTURE["status"]
    response._content = FIXTURE["body"]
    response.headers["Content-Type"] = "application/json"
    response.url = prepared.url
    response.request = prepared

    return response


class OfflineTokenApi:
    def __init__(
        self,
        classes,
        host=HOST,
        alter_http_token=False
    ):
        self.guard = TokenPayloadGuard(classes)
        self.host = host
        self.alter_http_token = alter_http_token

    def get_response(self, request):
        # First boundary: exact SDK class, fields, and budget.
        self.guard.check_sdk(request)

        from webull.core.http.response import Response

        protocol = Response.__init__.__globals__["PT"].HTTPS

        if request.get_method() == "GET":
            body = None
        else:
            fields = dict(request.get_body_params())

            if self.alter_http_token:
                fields["token"] = (
                    "DIFFERENT_SYNTHETIC_TOKEN"
                )

            body = json.dumps(
                fields,
                separators=(",", ":")
            ).encode("utf-8")

        http_response = Response(
            host=self.host,
            url=request.get_url(),
            method=request.get_method(),
            headers={
                "Accept": "application/json",
                "Content-Type": "application/json",
            },
            protocol=protocol,
            content=body,
            port=443,
            read_timeout=5,
            connect_timeout=5,
            verify=True,
        )

        GuardedSession.context = (self.guard, request)

        # SDK's actual HTTP response-processing method.
        status, headers, raw, response = (
            http_response.get_response_object()
        )

        require(
            status == response.status_code,
            "HTTP status changed"
        )
        require(
            raw == response.content,
            "HTTP response bytes changed"
        )

        return response


def make_request(classes, name):
    request = classes[name]()

    if name != "GetConfigRequest":
        request.set_token(FAKE_TOKEN)

    return request


def expect_block(label, callback):
    before = len(EVENTS)

    try:
        callback()
    except PermissionError:
        require(
            len(EVENTS) == before,
            "Rejected request reached HTTP fixture"
        )
        print(label + ": REJECTED")
        return

    raise AssertionError(
        "UNSAFE OPERATION ACCEPTED: " + label
    )


def main():
    print(
        "\n=== JAHVASCRIPT WEBULL TOKEN TRANSPORT AUDIT ==="
    )

    protected = verify_sources()
    require(SCHEDULE.is_file(), "Collection schedule missing")
    schedule_hash = sha(SCHEDULE)

    classes = load_request_classes()

    import webull.core.http.response as transport

    print("Frozen research fingerprints: VERIFIED")
    print("Installed Webull request classes: VERIFIED")

    with patch.object(
        transport, "Session", GuardedSession
    ), patch.object(
        requests.sessions.Session, "send", fake_send
    ), patch.object(
        socket.socket, "connect", block_network
    ), patch.object(
        socket.socket, "connect_ex", block_network
    ), patch.dict(
        os.environ,
        {
            "HTTP_DEBUG": "false",
            "WEBULL_HTTP_DEBUG": "false",
        }
    ):
        print("\n--- GUARDED SYNTHETIC TOKEN REQUESTS ---")

        api = OfflineTokenApi(classes)

        for name, (path, method) in ROUTES.items():
            response = api.get_response(
                make_request(classes, name)
            )

            require(
                response.status_code == 200,
                "Synthetic HTTP 200 failed"
            )
            require(
                response.json().get("synthetic") is True,
                "Synthetic JSON decoding failed"
            )
            require(
                EVENTS[-1] == (
                    method,
                    "https://" + HOST + path
                ),
                "Unexpected SDK HTTP route"
            )

            print(name + ": PASS")

        require(
            len(EVENTS) == 4,
            "Expected four synthetic HTTP sends"
        )

        print("\n--- INTEGRATED REJECTION TESTS ---")

        name = "CheckTokenRequest"

        expect_block(
            "Changed token between SDK and HTTP",
            lambda: OfflineTokenApi(
                classes,
                alter_http_token=True
            ).get_response(make_request(classes, name))
        )

        expect_block(
            "Unauthorized destination",
            lambda: OfflineTokenApi(
                classes,
                host="example.com"
            ).get_response(make_request(classes, name))
        )

        expect_block(
            "Missing token",
            lambda: OfflineTokenApi(
                classes
            ).get_response(classes[name]())
        )

        expect_block(
            "Unexpected historical-data request",
            lambda: OfflineTokenApi(
                classes
            ).get_response(
                classes["BatchHistoricalBarsRequest"]()
            )
        )

        limited = OfflineTokenApi(classes)

        limited.get_response(
            make_request(classes, "GetConfigRequest")
        )
        limited.get_response(
            make_request(classes, "GetConfigRequest")
        )

        expect_block(
            "Third configuration request",
            lambda: limited.get_response(
                make_request(classes, "GetConfigRequest")
            )
        )

        FIXTURE["status"] = 302
        FIXTURE["body"] = b""

        try:
            OfflineTokenApi(classes).get_response(
                make_request(classes, "GetConfigRequest")
            )
        except PermissionError:
            print("HTTP redirect: REJECTED")
        else:
            raise AssertionError(
                "HTTP redirect was accepted"
            )

        FIXTURE["status"] = 503
        FIXTURE["body"] = b'{"error":"synthetic_only"}'

        response = OfflineTokenApi(classes).get_response(
            make_request(classes, "GetConfigRequest")
        )

        require(
            response.status_code == 503,
            "HTTP error status not preserved"
        )
        require(
            response.content == FIXTURE["body"],
            "HTTP error bytes not preserved"
        )

        print("HTTP 503 status and bytes: PRESERVED")
        print("Real network connections: BLOCKED")

    require(
        verify_sources() == protected,
        "Frozen research fingerprints changed"
    )
    require(
        sha(SCHEDULE) == schedule_hash,
        "Collection schedule changed"
    )

    report = {
        "version": "WEBULL_TOKEN_TRANSPORT_AUDIT_V001",
        "scope": "OFFLINE_SYNTHETIC_ONLY",
        "collection_schedule_sha256": schedule_hash,
        "research_fingerprints": protected,
        "sdk_to_http_token_integration": "PASS",
        "four_bootstrap_requests": "PASS",
        "altered_token": "REJECTED",
        "unauthorized_destination": "REJECTED",
        "missing_token": "REJECTED",
        "historical_request_in_token_path": "REJECTED",
        "bootstrap_request_budget": "PASS",
        "redirect": "REJECTED",
        "http_503": "PRESERVED",
        "token_values_saved": False,
        "actual_sdk_token_manager_executed": False,
        "real_network_requests": 0,
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
        print("\nNew token transport audit: SAVED")

    print("\n--- FINAL RESULT ---")
    print("SDK-to-HTTP token validation: OFFLINE VERIFIED")
    print("Genuine authentication: NOT EXECUTED")
    print("Real SDK token manager: NOT EXECUTED")
    print("Actual Webull response schema: NOT VERIFIED")
    print("Real network requests: NONE")
    print("Credentials accessed: NONE")
    print("Original Research V001: UNCHANGED")
    print("Real sessions admitted: 0")
    print("Order execution: DISABLED")
    print("\nSTEP 15H.65U TOKEN TRANSPORT VERIFIED")


if __name__ == "__main__":
    main()
