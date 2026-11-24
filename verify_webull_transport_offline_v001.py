"""Offline-only Webull SDK HTTP transport regression test."""

import hashlib
import importlib
import json
import os
import socket
from pathlib import Path
from unittest.mock import patch

import requests

from forward_collection_schedule_v001 import verify_sources

ROOT = Path(__file__).resolve().parent
FORWARD = ROOT / "journal/forward_evaluation_v001"
SCHEDULE = FORWARD / "collection_schedule_v001.json"
REPORT = FORWARD / "webull_transport_fixture_audit_v001.json"


def require(ok, reason):
    if not ok:
        raise AssertionError(
            "WEBULL TRANSPORT AUDIT FAILED: " + reason
        )


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def block_network(*args, **kwargs):
    raise AssertionError(
        "NETWORK ACCESS ATTEMPTED DURING OFFLINE TEST"
    )


class OfflineSession:
    status = 200
    content = b""
    sends = 0

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_value, traceback):
        return False

    def prepare_request(self, request):
        return request.prepare()

    def send(self, prepared, **kwargs):
        require(
            prepared.url.startswith(
                "https://offline.invalid/"
            ),
            "Unexpected request destination"
        )

        type(self).sends += 1

        response = requests.Response()
        response.status_code = self.status
        response._content = self.content
        response.headers["Content-Type"] = "application/json"
        response.url = prepared.url
        response.request = prepared
        return response


def make_sdk_response(module):
    """Construct a genuine SDK Response for an offline fixture."""

    # Use the SDK's own protocol enum.
    # This does not instantiate an API client.
    protocol = module.Response.__init__.__globals__["PT"].HTTPS

    return module.Response(
        host="offline.invalid",
        url="/fixture",
        method="GET",
        headers={"Accept": "application/json"},
        protocol=protocol,
        content=b"",
        port=443,
        read_timeout=5,
        connect_timeout=5,
        verify=True,
    )


def exercise(module, status, content):
    OfflineSession.status = status
    OfflineSession.content = content
    OfflineSession.sends = 0

    result = make_sdk_response(
        module
    ).get_response_object()

    require(
        isinstance(result, tuple) and len(result) == 4,
        "SDK transport returned unexpected structure"
    )

    actual_status, headers, body, response = result

    require(
        actual_status == status,
        "HTTP status mismatch"
    )
    require(
        response.status_code == status,
        "Response object status mismatch"
    )
    require(
        response.content == content,
        "Original HTTP bytes were changed"
    )
    require(
        isinstance(headers, dict)
        or hasattr(headers, "get"),
        "Response headers unavailable"
    )
    require(
        headers.get("Content-Type") == "application/json",
        "Content-Type was lost"
    )
    require(
        OfflineSession.sends == 1,
        "Unexpected number of fake HTTP requests"
    )

    # Confirm the transport body also represents
    # exactly the fixture that entered the SDK.
    if isinstance(body, bytes):
        require(body == content, "Transport body mismatch")
    elif isinstance(body, str):
        require(
            body.encode("utf-8") == content,
            "Decoded transport body mismatch"
        )
    else:
        raise AssertionError(
            "Unrecognized transport body type: "
            + type(body).__name__
        )

    return response


def main():
    print(
        "\n=== JAHVASCRIPT WEBULL OFFLINE TRANSPORT AUDIT ==="
    )
    print("Regression fix: CONTEXT MANAGER SUPPORTED")

    before = verify_sources()
    require(SCHEDULE.is_file(), "Schedule missing")

    schedule_sha = sha(SCHEDULE)

    module = importlib.import_module(
        "webull.core.http.response"
    )
    sdk_sha = sha(Path(module.__file__))

    print("Protected research: VERIFIED")
    print("SDK response module: FOUND")

    with patch.object(module, "Session", OfflineSession), \
         patch.object(socket.socket, "connect", block_network), \
         patch.object(socket.socket, "connect_ex", block_network), \
         patch.dict(os.environ, {
             "HTTP_DEBUG": "false",
             "WEBULL_HTTP_DEBUG": "false"
         }):

        print("\n--- OFFLINE SYNTHETIC TESTS ---")

        good = (
            b'{"fixture":"synthetic_only",'
            b'"bars":[{"symbol":"SPY","close":"123.45"}]}'
        )

        response = exercise(module, 200, good)
        require(
            response.json()["fixture"] == "synthetic_only",
            "Valid JSON decoding failed"
        )
        print("HTTP 200 response: PASS")
        print("JSON decoding: PASS")
        print("Original response bytes: PRESERVED")

        response = exercise(
            module, 200, b'{"incomplete": '
        )
        try:
            response.json()
        except ValueError:
            print("Malformed JSON: REJECTED")
        else:
            raise AssertionError(
                "Malformed JSON unexpectedly accepted"
            )

        response = exercise(
            module,
            503,
            b'{"error":"synthetic_http_error"}'
        )
        require(
            response.status_code == 503,
            "HTTP error incorrectly converted to success"
        )
        print("HTTP 503 response: PRESERVED")

    require(
        verify_sources() == before,
        "Protected research fingerprints changed"
    )
    require(
        sha(SCHEDULE) == schedule_sha,
        "Collection schedule changed"
    )
    require(
        sha(Path(module.__file__)) == sdk_sha,
        "Installed SDK file changed"
    )

    report = {
        "version": "WEBULL_TRANSPORT_FIXTURE_AUDIT_V001",
        "sdk_response_module_sha256": sdk_sha,
        "collection_schedule_sha256": schedule_sha,
        "research_source_fingerprints": before,
        "synthetic_http_200": "PASS",
        "synthetic_malformed_json": "REJECTED",
        "synthetic_http_503": "PRESERVED",
        "raw_response_bytes": "VERIFIED_IN_FIXTURES",
        "network_requests": 0,
        "live_webull_schema_verified": False,
        "real_sessions_admitted": 0,
        "execution_enabled": False,
    }

    REPORT.parent.mkdir(parents=True, exist_ok=True)

    if REPORT.exists():
        require(
            REPORT.is_file()
            and not REPORT.is_symlink()
            and json.loads(REPORT.read_text()) == report,
            "Existing audit differs; original protected"
        )
        print("Existing audit: VERIFIED, NOT OVERWRITTEN")
    else:
        os.umask(0o077)
        with REPORT.open("x") as handle:
            json.dump(report, handle, indent=2)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        REPORT.chmod(0o600)
        print("New audit: SAVED")

    print("\n--- FINAL RESULT ---")
    print("Synthetic response handling: PASS")
    print("Live Webull HTTP format: NOT VERIFIED")
    print("Real sessions admitted: 0")
    print("Webull requests: NONE")
    print("Yahoo requests: NONE")
    print("AI requests: NONE")
    print("Order execution: DISABLED")
    print("\nSTEP 15H.65F VERIFIED")


if __name__ == "__main__":
    main()
