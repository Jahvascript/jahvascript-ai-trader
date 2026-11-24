"""Fully offline destination-policy regression tests."""

import socket
from unittest.mock import patch

import requests

from forward_collection_schedule_v001 import verify_sources
from webull_probe_destination_guard_v001 import (
    HOST, MARKET_PATH, MARKET_BODY,
    check_prepared_request, check_response,
)


def blocked(*args, **kwargs):
    raise AssertionError("REAL NETWORK REQUEST BLOCKED")


def prepared(path=MARKET_PATH, host=HOST, scheme="https",
             method="POST", body=None):
    return requests.Request(
        method,
        f"{scheme}://{host}{path}",
        json=MARKET_BODY if body is None else body
    ).prepare()


def rejection(label, action):
    try:
        action()
    except (PermissionError, ValueError):
        print(label + ": REJECTED")
        return
    raise AssertionError("UNSAFE INPUT ACCEPTED: " + label)


print("\n=== JAHVASCRIPT WEBULL DESTINATION GUARD AUDIT ===")

before = verify_sources()

with patch.object(
    requests.sessions.Session, "send", blocked
), patch.object(
    socket.socket, "connect", blocked
), patch.object(
    socket.socket, "connect_ex", blocked
):
    good = prepared()

    assert check_prepared_request(good) == MARKET_PATH

    print("\n--- APPROVED SYNTHETIC REQUEST ---")
    print("HTTPS: VERIFIED")
    print("Pinned hostname:", HOST)
    print("API path:", MARKET_PATH)
    print("Symbol: SPY")
    print("Timeframe: M5")
    print("Count: 1")
    print("Trading session: RTH")
    print("Market-data request: PASS")

    print("\n--- DESTINATION REJECTION TESTS ---")

    rejection(
        "Unapproved external host",
        lambda: check_prepared_request(
            prepared(host="example.com")
        )
    )

    rejection(
        "Lookalike Webull hostname",
        lambda: check_prepared_request(
            prepared(host=HOST + ".example.com")
        )
    )

    rejection(
        "Production host during sandbox test",
        lambda: check_prepared_request(
            prepared(host="api.webull.com")
        )
    )

    rejection(
        "Unencrypted HTTP",
        lambda: check_prepared_request(
            prepared(scheme="http")
        )
    )

    rejection(
        "Unexpected port",
        lambda: check_prepared_request(
            prepared(host=HOST + ":8443")
        )
    )

    rejection(
        "Unexpected API path",
        lambda: check_prepared_request(
            prepared(path="/trade/order/place")
        )
    )

    rejection(
        "Wrong HTTP method",
        lambda: check_prepared_request(
            prepared(method="GET")
        )
    )

    rejection(
        "Unexpected query parameter",
        lambda: check_prepared_request(
            prepared(path=MARKET_PATH + "?extra=1")
        )
    )

    bad_body = dict(MARKET_BODY)
    bad_body["symbols"] = ["QQQ"]

    rejection(
        "Unauthorized symbol",
        lambda: check_prepared_request(
            prepared(body=bad_body)
        )
    )

    bad_body = dict(MARKET_BODY)
    bad_body["count"] = "100"

    rejection(
        "Excess historical candles",
        lambda: check_prepared_request(
            prepared(body=bad_body)
        )
    )

    bad_body = dict(MARKET_BODY)
    bad_body["real_time_required"] = True

    rejection(
        "Additional real-time parameter",
        lambda: check_prepared_request(
            prepared(body=bad_body)
        )
    )

    response = requests.Response()
    response.status_code = 200
    response.url = good.url
    response.history = []

    assert check_response(response, good.url)

    print("\nHTTP 200 response destination: PASS")

    response.status_code = 302

    rejection(
        "HTTP redirect",
        lambda: check_response(response, good.url)
    )

    response.status_code = 200
    response.url = "https://example.com/other"

    rejection(
        "Changed response destination",
        lambda: check_response(response, good.url)
    )

    print("\nActual HTTP sends: BLOCKED")

assert verify_sources() == before

print("\n--- FINAL RESULT ---")
print("HTTPS destination policy: OFFLINE VERIFIED")
print("Approved host: SANDBOX ONLY")
print("Unexpected API paths: REJECTED")
print("Unexpected market-data parameters: REJECTED")
print("Redirects: REJECTED BY POLICY TEST")
print("SDK transport enforcement: NOT YET INTEGRATED")
print("Live Webull schema: NOT VERIFIED")
print("Webull network requests: NONE")
print("Research V001: UNCHANGED")
print("Real sessions admitted: 0")
print("Order execution: DISABLED")
print("\nSTEP 15H.65N DESTINATION GUARD VERIFIED")
