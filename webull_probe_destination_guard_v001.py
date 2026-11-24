"""Strict HTTPS destination and payload guard for an offline Webull probe.

Not yet connected to a live transport. Does not enable network access.
"""

import json
from urllib.parse import urlsplit

HOST = "api.sandbox.webull.com"

METHODS = {
    "/openapi/config": "GET",
    "/auth/tokens/check": "POST",
    "/auth/tokens/create": "POST",
    "/openapi/auth/token/refresh": "POST",
    "/market-data/stocks/bars/list": "POST",
}

MARKET_PATH = "/market-data/stocks/bars/list"

MARKET_BODY = {
    "symbols": ["SPY"],
    "category": "US_STOCK",
    "timespan": "M5",
    "count": "1",
    "trading_sessions": "RTH",
}


def require(condition, message):
    if not condition:
        raise PermissionError(
            "WEBULL DESTINATION BLOCKED: " + message
        )


def unique_object(pairs):
    result = {}
    for key, value in pairs:
        require(key not in result, "Duplicate JSON field")
        result[key] = value
    return result


def check_prepared_request(prepared):
    """Validate an already constructed HTTP request."""

    url = urlsplit(prepared.url)

    require(url.scheme == "https", "HTTPS required")
    require(url.hostname == HOST, "Unapproved hostname")
    require(url.port in (None, 443), "Unapproved port")
    require(not url.username and not url.password,
            "URL credentials forbidden")
    require(not url.query and not url.fragment,
            "URL query or fragment forbidden")

    require(url.path in METHODS, "Unapproved API path")
    require(prepared.method == METHODS[url.path],
            "Unapproved HTTP method")

    if url.path == MARKET_PATH:
        require(
            isinstance(prepared.body, (bytes, str)),
            "Market-data JSON body required"
        )

        body = json.loads(
            prepared.body,
            object_pairs_hook=unique_object
        )

        require(type(body) is dict, "JSON object required")
        require(body == MARKET_BODY,
                "Unexpected market-data parameters")

        # Python equality treats True as equal to 1.
        # Type checks prevent this subtle ambiguity.
        require(
            all(
                type(body[key]) is type(value)
                for key, value in MARKET_BODY.items()
            ),
            "Market-data field type mismatch"
        )

    return url.path


def check_response(response, original_url):
    """Reject redirects and responses from changed destinations."""

    require(
        not (300 <= response.status_code < 400),
        "HTTP redirect forbidden"
    )
    require(
        not response.history,
        "Redirect history forbidden"
    )
    require(
        response.url == original_url,
        "Response URL changed"
    )
    return True
