"""Verify Webull's historical market-data SDK path offline."""

import argparse
import hashlib
import importlib.metadata
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
AUDIT = FORWARD / "webull_data_only_path_audit_v001.json"


def require(condition, message):
    if not condition:
        raise AssertionError(
            "DATA-ONLY SDK AUDIT FAILED: " + message
        )


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def block_network(*args, **kwargs):
    raise AssertionError(
        "REAL NETWORK ACCESS BLOCKED"
    )


class OfflineApiClient:
    """Fake client accepts historical requests only."""

    def __init__(self):
        self.requests_seen = []
        self.status = 200
        self.payload = b"{}"

    def set_stream_logger(self, *args, **kwargs):
        """No-op logger for the offline SDK test."""
        return None

    def set_file_logger(self, *args, **kwargs):
        """No-op: never create log files in offline tests."""
        return None

    def get_response(self, request):
        request_type = type(request).__name__

        require(
            request_type == "BatchHistoricalBarsRequest",
            "Unexpected SDK request: " + request_type
        )

        self.requests_seen.append(request_type)

        response = requests.Response()
        response.status_code = self.status
        response._content = self.payload
        response.headers["Content-Type"] = "application/json"
        response.url = "https://offline.invalid/fixture"

        return response


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--live", action="store_true")
    args = parser.parse_args()

    if args.live:
        raise SystemExit(
            "LIVE REQUEST BLOCKED: this test is offline only"
        )

    print("\n=== JAHVASCRIPT DATA-ONLY WEBULL SDK AUDIT ===")

    protected = verify_sources()

    require(SCHEDULE.is_file(), "Schedule missing")
    schedule_hash = sha(SCHEDULE)

    version = importlib.metadata.version(
        "webull-openapi-python-sdk"
    )

    require(
        version == "3.0.3",
        "SDK version differs from inspected version"
    )

    print("Webull SDK:", version)
    print("Frozen research: VERIFIED")
    print("Collection schedule: VERIFIED")

    from webull.data.data_client import DataClient

    fake = OfflineApiClient()

    sample = (
        b'{"synthetic":true,"data":['
        b'{"symbol":"SPY","bars":['
        b'{"timestamp":"2026-10-22T13:30:00Z",'
        b'"open":"123.40","high":"123.50",'
        b'"low":"123.30","close":"123.45",'
        b'"volume":"100"}]}]}'
    )

    fake.payload = sample

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
        # The actual SDK market-data client is used,
        # but its underlying API client is fake.
        # Skip configuration/token initialization only in
        # this synthetic offline test. No real credentials.
        with patch(
            'webull.data.data_client.ClientInitializer.initializer',
            return_value=None
        ):
            data = DataClient(fake)

        response = data.market_data.get_history_bar(
            symbol="SPY",
            category="US_STOCK",
            timespan="M5",
            count="1",
            real_time_required=False,
            trading_sessions="RTH"
        )

        require(
            response.status_code == 200,
            "Synthetic HTTP 200 not preserved"
        )

        require(
            response.content == sample,
            "Response bytes changed"
        )

        require(
            fake.requests_seen
                == ["BatchHistoricalBarsRequest"],
            "Unexpected historical request path"
        )

        decoded = response.json()

        require(
            decoded.get("synthetic") is True,
            "Synthetic fixture marker missing"
        )

        print("\n--- OFFLINE HISTORICAL REQUEST ---")
        print("DataClient initialization: PASS")
        print("get_history_bar(): PASS")
        print("BatchHistoricalBarsRequest: VERIFIED")
        print("Synthetic HTTP 200: PASS")
        print("Response bytes: PRESERVED")
        print("Synthetic JSON decoding: PASS")

        fake.status = 503
        fake.payload = b'{"error":"synthetic_failure"}'

        response = data.market_data.get_history_bar(
            symbol="SPY",
            category="US_STOCK",
            timespan="M5",
            count="1",
            real_time_required=False,
            trading_sessions="RTH"
        )

        require(
            response.status_code == 503,
            "Synthetic HTTP 503 not preserved"
        )

        require(
            len(fake.requests_seen) == 2,
            "Unexpected number of SDK requests"
        )

        print("Synthetic HTTP 503: PRESERVED")
        print("Unexpected SDK operations: NONE")
        print("Network connections: BLOCKED")

    require(
        verify_sources() == protected,
        "Protected research fingerprints changed"
    )

    require(
        sha(SCHEDULE) == schedule_hash,
        "Collection schedule modified"
    )

    report = {
        "version": "WEBULL_DATA_ONLY_PATH_AUDIT_V001",
        "sdk_version": version,
        "collection_schedule_sha256": schedule_hash,
        "research_fingerprints": protected,
        "historical_sdk_request":
            "BatchHistoricalBarsRequest",
        "synthetic_http_200": "PASS",
        "synthetic_http_503": "PASS",
        "synthetic_json": "PASS",
        "network_requests": 0,
        "live_webull_schema_verified": False,
        "real_sessions_admitted": 0,
        "trade_client_instantiated": False,
        "execution_enabled": False,
        "trade_authorization": False
    }

    AUDIT.parent.mkdir(parents=True, exist_ok=True)

    if AUDIT.exists():
        require(
            AUDIT.is_file()
            and not AUDIT.is_symlink()
            and json.loads(AUDIT.read_text()) == report,
            "Existing audit differs; original protected"
        )
        print("\nExisting audit: VERIFIED, NOT OVERWRITTEN")
    else:
        os.umask(0o077)

        with AUDIT.open("x") as handle:
            json.dump(report, handle, indent=2)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())

        AUDIT.chmod(0o600)
        print("\nNew audit: SAVED")

    print("\n--- FINAL RESULT ---")
    print("Historical SDK path: OFFLINE VERIFIED")
    print("Live Webull schema: NOT VERIFIED")
    print("Webull network requests: NONE")
    print("Credentials accessed: NONE")
    print("TradeClient instantiated: NO")
    print("Research V001: UNCHANGED")
    print("Real sessions admitted: 0")
    print("Order execution: DISABLED")
    print("\nSTEP 15H.65I VERIFIED")


if __name__ == "__main__":
    main()
