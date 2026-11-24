"""Offline, fail-closed Webull historical probe parameter audit."""

import socket
from pathlib import Path
from unittest.mock import patch

import requests

from forward_collection_schedule_v001 import verify_sources
from webull_probe_request_guard_v001 import load_request_classes

EXPECTED_PATH = "/market-data/stocks/bars/list"
EXPECTED_FIELDS = frozenset({
    "symbols",
    "category",
    "timespan",
    "count",
    "trading_sessions",
})


def require(condition, message):
    if not condition:
        raise PermissionError(
            "HISTORICAL PROBE REQUEST BLOCKED: " + message
        )


def deny_network(*args, **kwargs):
    raise AssertionError(
        "REAL NETWORK ACCESS ATTEMPTED"
    )


def make_request(request_class):
    """Construct one synthetic historical request."""
    request = request_class()

    request.set_symbols(["SPY"])
    request.set_category("US_STOCK")
    request.set_timespan("M5")
    request.set_count("1")
    request.set_real_time_required(False)
    request.set_trading_sessions("RTH")

    return request


def validate_request(request, expected_class):
    """Validate SDK identity, endpoint and serialized parameters."""

    require(
        type(request) is expected_class,
        "Unapproved SDK request class"
    )

    require(
        request.get_method() == "POST",
        "Unexpected HTTP method"
    )

    require(
        request.get_action_name() == EXPECTED_PATH,
        "Unexpected API operation"
    )

    require(
        request.get_url() == EXPECTED_PATH,
        "Unexpected URL or query"
    )

    require(
        request.get_endpoint() is None,
        "Unexpected explicit endpoint"
    )

    require(
        request.get_query_params() == {},
        "Unexpected query parameters"
    )

    require(
        request.get_content() is None,
        "Unexpected raw request content"
    )

    headers = request.get_headers()

    require(
        isinstance(headers, dict)
        and set(headers) == {"x-version"},
        "Unexpected SDK request headers"
    )

    body = request.get_body_params()

    require(
        isinstance(body, dict),
        "Request body must be an object"
    )

    require(
        set(body) == EXPECTED_FIELDS,
        "Unexpected or missing body fields"
    )

    require(
        body["symbols"] in (["SPY"], "SPY"),
        "Only SPY is permitted"
    )

    require(
        body["category"] == "US_STOCK",
        "Only US_STOCK is permitted"
    )

    require(
        body["timespan"] == "M5",
        "Only M5 is permitted"
    )

    require(
        str(body["count"]) == "1"
        and not isinstance(body["count"], bool),
        "Only one historical candle is permitted"
    )

    require(
        body["trading_sessions"] == "RTH",
        "Only regular trading hours are permitted"
    )

    return True


def expect_rejection(label, request, expected_class):
    try:
        validate_request(request, expected_class)
    except PermissionError:
        print(label + ": REJECTED")
        return

    raise AssertionError(
        "UNSAFE REQUEST WAS ACCEPTED: " + label
    )


def main():
    print(
        "\n=== JAHVASCRIPT WEBULL PARAMETER GUARD AUDIT ==="
    )

    before = verify_sources()

    with patch.object(
        requests.sessions.Session,
        "send",
        deny_network
    ), patch.object(
        socket.socket,
        "connect",
        deny_network
    ), patch.object(
        socket.socket,
        "connect_ex",
        deny_network
    ):
        classes = load_request_classes()
        cls = classes["BatchHistoricalBarsRequest"]

        good = make_request(cls)
        validate_request(good, cls)

        print("\n--- APPROVED SYNTHETIC REQUEST ---")
        print("SDK request class: VERIFIED")
        print("HTTP method: POST")
        print("API path:", EXPECTED_PATH)
        print("Symbol: SPY")
        print("Category: US_STOCK")
        print("Timeframe: M5")
        print("Count: 1")
        print("Trading session: RTH")
        print("Unknown body fields: NONE")
        print("Expected request structure: PASS")

        print("\n--- REQUEST REJECTION TESTS ---")

        modifications = [
            ("Different symbol", "symbols", ["QQQ"]),
            ("Multiple symbols", "symbols", ["SPY", "QQQ"]),
            ("Wrong category", "category", "US_OPTION"),
            ("Wrong timeframe", "timespan", "D"),
            ("Excess candle count", "count", "2"),
            ("Wrong trading session", "trading_sessions", "ALL"),
            ("Unexpected extra field", "unexpected", "value"),
            ("Real-time field", "real_time_required", True),
        ]

        for label, key, value in modifications:
            request = make_request(cls)
            original = request.get_body_params()
            altered = dict(original)
            altered[key] = value

            with patch.object(
                request,
                "get_body_params",
                return_value=altered
            ):
                expect_rejection(label, request, cls)

        expect_rejection(
            "Wrong SDK request class",
            object(),
            cls
        )

        # Test the actual SDK's serialization of True,
        # not merely a manually altered dictionary.
        realtime = make_request(cls)
        realtime.set_real_time_required(True)

        try:
            validate_request(realtime, cls)
        except PermissionError:
            print(
                "SDK real-time=True serialization: REJECTED"
            )
            realtime_verified = True
        else:
            print(
                "SDK real-time=True serialization: "
                "NOT DISTINGUISHABLE FROM ALLOWED REQUEST"
            )
            realtime_verified = False

        require(
            realtime_verified,
            "SDK real-time flag needs additional inspection"
        )

        print("\nNetwork transport: BLOCKED")

    assert verify_sources() == before, (
        "Protected research fingerprints changed"
    )

    print("\n--- FINAL RESULT ---")
    print("Historical request parameters: OFFLINE VERIFIED")
    print("Unexpected parameters: REJECTED")
    print("Destination host restriction: NOT YET VERIFIED")
    print("Actual Webull HTTP schema: NOT VERIFIED")
    print("Credentials accessed: NONE")
    print("Webull network requests: NONE")
    print("Research V001: UNCHANGED")
    print("Real sessions admitted: 0")
    print("Order execution: DISABLED")

    print("\nSTEP 15H.65M PARAMETER GUARD VERIFIED")


if __name__ == "__main__":
    main()
