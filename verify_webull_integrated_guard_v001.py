"""Offline integration test: Webull SDK + request + HTTPS guards.

No real API client, signing, credentials, socket access, or trading.
"""

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
    ProbeRequestGuard,
)
from webull_probe_destination_guard_v001 import (
    HOST,
    MARKET_PATH,
    check_prepared_request,
    check_response,
)

ROOT = Path(__file__).resolve().parent
FORWARD = ROOT / "journal/forward_evaluation_v001"
REPORT = FORWARD / "webull_integrated_guard_audit_v001.json"
SCHEDULE = FORWARD / "collection_schedule_v001.json"

EVENTS = []
FIXTURE = {
    "status": 200,
    "content": b'{"synthetic":true,"data":[]}',
}


def require(condition, reason):
    if not condition:
        raise AssertionError("INTEGRATED GUARD FAILED: " + reason)


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def no_network(*args, **kwargs):
    raise AssertionError("REAL NETWORK CONNECTION BLOCKED")


def expect_block(label, callback):
    before = len(EVENTS)

    try:
        callback()
    except PermissionError:
        require(
            len(EVENTS) == before,
            "Disallowed request reached fixture transport"
        )
        print(label + ": REJECTED")
        return

    raise AssertionError("UNSAFE REQUEST ACCEPTED: " + label)


class GuardedSession(requests.Session):
    """Guard every prepared HTTP request before sending."""

    def send(self, prepared, **kwargs):
        path = check_prepared_request(prepared)

        # This rehearsal intentionally refuses SDK bootstrap
        # endpoints. They need separate validation before live use.
        if path != MARKET_PATH:
            raise PermissionError(
                "BOOTSTRAP NETWORK REQUEST NOT AUTHORIZED"
            )

        # Never follow redirects.
        kwargs["allow_redirects"] = False

        response = super().send(prepared, **kwargs)

        check_response(response, prepared.url)
        return response


def offline_send(self, prepared, **kwargs):
    """Synthetic replacement for requests.Session.send."""

    require(
        isinstance(self, GuardedSession),
        "Unexpected session class"
    )
    require(
        kwargs.get("allow_redirects") is False,
        "Redirects must be disabled"
    )
    require(
        prepared.url.startswith("https://" + HOST + "/"),
        "Unexpected fixture destination"
    )

    EVENTS.append(prepared.url)

    response = requests.Response()
    response.status_code = FIXTURE["status"]
    response._content = FIXTURE["content"]
    response.headers["Content-Type"] = "application/json"
    response.url = prepared.url
    response.request = prepared
    return response


class GuardedOfflineApi:
    """Only accepts one genuine SDK historical request."""

    def __init__(self, classes, host=HOST):
        self.guard = ProbeRequestGuard(classes)
        self.host = host

    def set_stream_logger(self, *args, **kwargs):
        return None

    def set_file_logger(self, *args, **kwargs):
        return None

    def get_response(self, request):
        # First boundary: exact SDK class and request budget.
        kind = self.guard.permit(request)

        require(
            kind == "HISTORICAL_MARKET_DATA",
            "Unexpected SDK request category"
        )

        # Build an actual installed SDK Response object,
        # using only synthetic, non-sensitive input.
        from webull.core.http.response import Response

        protocol = Response.__init__.__globals__["PT"].HTTPS

        body = json.dumps(
            request.get_body_params(),
            separators=(",", ":")
        ).encode("utf-8")

        sdk_response = Response(
            host=self.host,
            url=request.get_url(),
            method=request.get_method(),
            headers={
                "Content-Type": "application/json",
                "Accept": "application/json",
            },
            protocol=protocol,
            content=body,
            port=443,
            read_timeout=5,
            connect_timeout=5,
            verify=True,
        )

        # Second boundary: GuardedSession validates the final
        # prepared HTTP request before the fake transport.
        status, headers, raw, response = (
            sdk_response.get_response_object()
        )

        require(
            status == response.status_code,
            "SDK response status mismatch"
        )
        require(
            raw == response.content,
            "SDK response bytes mismatch"
        )

        return response


def historical(data, symbol="SPY"):
    return data.market_data.get_history_bar(
        symbol=symbol,
        category="US_STOCK",
        timespan="M5",
        count="1",
        real_time_required=False,
        trading_sessions="RTH",
    )


def main():
    print("\n=== JAHVASCRIPT INTEGRATED WEBULL GUARD AUDIT ===")

    protected = verify_sources()
    require(SCHEDULE.is_file(), "Schedule missing")
    schedule_hash = sha(SCHEDULE)

    from webull.data.data_client import DataClient
    import webull.core.http.response as transport

    classes = load_request_classes()

    print("Frozen research fingerprints: VERIFIED")
    print("SDK request classes: VERIFIED")
    print("Destination guard: LOADED")

    # All actual network methods are blocked or replaced.
    # SDK initialization is mocked for this offline test only.
    with patch(
        "webull.data.data_client.ClientInitializer.initializer",
        return_value=None
    ), patch.object(
        transport, "Session", GuardedSession
    ), patch.object(
        requests.sessions.Session, "send", offline_send
    ), patch.object(
        socket.socket, "connect", no_network
    ), patch.object(
        socket.socket, "connect_ex", no_network
    ):

        print("\n--- END-TO-END SYNTHETIC REQUEST ---")

        api = GuardedOfflineApi(classes)
        data = DataClient(api)

        EVENTS.clear()
        FIXTURE["status"] = 200
        FIXTURE["content"] = (
            b'{"synthetic":true,"data":[]}'
        )

        response = historical(data)

        require(
            response.status_code == 200,
            "Synthetic HTTP 200 failed"
        )
        require(
            response.content == FIXTURE["content"],
            "Response bytes changed"
        )
        require(
            response.json().get("synthetic") is True,
            "Synthetic JSON decoding failed"
        )
        require(
            len(EVENTS) == 1,
            "Expected exactly one intercepted HTTP send"
        )

        print("Actual SDK historical request builder: PASS")
        print("Exact SDK request-class guard: PASS")
        print("Final prepared HTTPS request guard: PASS")
        print("SDK HTTP response object: PASS")
        print("Response bytes preserved: PASS")
        print("Synthetic HTTP 200 JSON: PASS")

        print("\n--- REJECTION AND FAILURE TESTS ---")

        expect_block(
            "Second historical request",
            lambda: historical(data)
        )

        bad_api = GuardedOfflineApi(classes)
        bad_data = DataClient(bad_api)

        expect_block(
            "Unauthorized QQQ symbol",
            lambda: historical(bad_data, "QQQ")
        )

        wrong_host = GuardedOfflineApi(
            classes,
            host="example.com"
        )
        wrong_data = DataClient(wrong_host)

        expect_block(
            "Unauthorized destination host",
            lambda: historical(wrong_data)
        )

        unexpected = GuardedOfflineApi(classes)

        expect_block(
            "Unknown SDK request class",
            lambda: unexpected.get_response(object())
        )

        FIXTURE["status"] = 302
        FIXTURE["content"] = b""

        redirect_api = GuardedOfflineApi(classes)
        redirect_data = DataClient(redirect_api)

        try:
            historical(redirect_data)
        except PermissionError:
            print("HTTP redirect response: REJECTED")
        else:
            raise AssertionError(
                "Redirect response unexpectedly accepted"
            )

        FIXTURE["status"] = 503
        FIXTURE["content"] = (
            b'{"error":"synthetic_service_unavailable"}'
        )

        error_api = GuardedOfflineApi(classes)
        error_data = DataClient(error_api)
        error_response = historical(error_data)

        require(
            error_response.status_code == 503,
            "HTTP 503 status was changed"
        )
        require(
            error_response.content == FIXTURE["content"],
            "HTTP error body was changed"
        )

        print("HTTP 503 status and bytes: PRESERVED")
        print("Real network connections: BLOCKED")

    require(
        verify_sources() == protected,
        "Research fingerprints changed"
    )
    require(
        sha(SCHEDULE) == schedule_hash,
        "Collection schedule changed"
    )

    report = {
        "version": "WEBULL_INTEGRATED_GUARD_AUDIT_V001",
        "scope": "OFFLINE_SYNTHETIC_ONLY",
        "collection_schedule_sha256": schedule_hash,
        "research_fingerprints": protected,
        "sdk_historical_request": "PASS",
        "request_class_guard": "PASS",
        "prepared_https_request_guard": "PASS",
        "response_integrity": "PASS",
        "second_request": "REJECTED",
        "unauthorized_symbol": "REJECTED",
        "unauthorized_host": "REJECTED",
        "unknown_sdk_request": "REJECTED",
        "redirect": "REJECTED",
        "http_503": "PRESERVED",
        "bootstrap_transport_authorized": False,
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
        print("\nNew integration audit: SAVED")

    print("\n--- FINAL RESULT ---")
    print("SDK-to-HTTP guard integration: OFFLINE VERIFIED")
    print("Authentication/bootstrap: NOT YET INTEGRATED")
    print("Live response schema: NOT VERIFIED")
    print("Actual network requests: NONE")
    print("Research V001: UNCHANGED")
    print("Real sessions admitted: 0")
    print("Order execution: DISABLED")
    print("\nSTEP 15H.65O INTEGRATED GUARD VERIFIED")


if __name__ == "__main__":
    main()
