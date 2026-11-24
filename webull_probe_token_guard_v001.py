"""Offline Webull SDK token-payload policy.

Does not authenticate, read credentials, or enable networking.
"""

import json
from urllib.parse import urlsplit

from webull_probe_request_guard_v001 import ProbeRequestGuard
from webull_probe_destination_guard_v001 import (
    HOST,
    check_prepared_request,
    check_response,
)

ROUTES = {
    "GetConfigRequest": ("/openapi/config", "GET"),
    "CheckTokenRequest": ("/auth/tokens/check", "POST"),
    "CreateTokenRequest": ("/auth/tokens/create", "POST"),
    "RefreshTokenRequest": (
        "/openapi/auth/token/refresh", "POST"
    ),
}


def require(ok, reason):
    if not ok:
        raise PermissionError(
            "TOKEN PAYLOAD BLOCKED: " + reason
        )


def unique_object(pairs):
    result = {}
    for key, value in pairs:
        require(key not in result, "Duplicate JSON field")
        result[key] = value
    return result


def valid_body(name, body):
    if name == "GetConfigRequest":
        require(body is None, "Config request must have no body")
        return None

    require(type(body) is dict, "Token body must be an object")
    require(set(body) == {"token"}, "Unexpected token fields")

    token = body["token"]

    require(type(token) is str, "Token must be a string")
    require(0 < len(token) <= 4096, "Invalid token length")
    require(not token.isspace(), "Blank token")

    return body


class TokenPayloadGuard:
    def __init__(self, classes):
        self.classes = classes
        self.budget = ProbeRequestGuard(classes)
        self.counts = {name: 0 for name in ROUTES}

    def request_fields(self, request):
        name = type(request).__name__

        require(
            name in ROUTES
            and self.classes.get(name) is type(request),
            "Unknown SDK class"
        )

        path, method = ROUTES[name]

        require(
            request.get_action_name() == path,
            "Unexpected SDK action"
        )
        require(
            request.get_method() == method,
            "Unexpected SDK method"
        )
        require(
            request.get_url() == path,
            "Unexpected SDK URL"
        )
        require(
            request.get_endpoint() is None,
            "Explicit endpoint forbidden"
        )
        require(
            request.get_query_params() == {},
            "Query parameters forbidden"
        )
        require(
            request.get_content() is None,
            "Unexpected raw content"
        )

        headers = request.get_headers()

        require(
            type(headers) is dict
            and set(headers) == {"x-version"},
            "Unexpected SDK headers"
        )

        body = valid_body(
            name,
            request.get_body_params()
        )

        return name, path, method, body

    def check_sdk(self, request):
        name, path, method, body = self.request_fields(request)

        require(
            self.counts[name] < 2,
            "Operation request limit exceeded"
        )

        require(
            self.budget.permit(request) == "SDK_INITIALIZATION",
            "Non-bootstrap request"
        )

        self.counts[name] += 1
        return name

    def check_prepared(self, prepared, sdk_request):
        name, path, method, expected = self.request_fields(
            sdk_request
        )

        actual_path = check_prepared_request(prepared)

        require(actual_path == path, "Path mismatch")
        require(prepared.method == method, "Method mismatch")

        parsed = urlsplit(prepared.url)

        require(
            parsed.scheme == "https"
            and parsed.hostname == HOST
            and parsed.port in (None, 443),
            "Unauthorized destination"
        )

        if name == "GetConfigRequest":
            require(
                prepared.body in (None, b"", ""),
                "Unexpected configuration body"
            )
            return name

        content_type = prepared.headers.get(
            "Content-Type", ""
        )

        require(
            content_type.split(";")[0].strip().lower()
            == "application/json",
            "JSON Content-Type required"
        )

        raw = prepared.body

        require(
            isinstance(raw, (bytes, str)),
            "Missing JSON body"
        )

        if isinstance(raw, bytes):
            require(len(raw) <= 8192, "Body too large")
            try:
                raw = raw.decode("utf-8")
            except UnicodeError:
                raise PermissionError(
                    "TOKEN PAYLOAD BLOCKED: Invalid UTF-8"
                )
        else:
            require(
                len(raw.encode("utf-8")) <= 8192,
                "Body too large"
            )

        try:
            actual = json.loads(
                raw,
                object_pairs_hook=unique_object
            )
        except (ValueError, TypeError):
            raise PermissionError(
                "TOKEN PAYLOAD BLOCKED: Invalid JSON"
            )

        valid_body(name, actual)

        require(
            actual == expected,
            "HTTP body differs from SDK request"
        )

        return name

    def check_result(self, response, original_url):
        return check_response(response, original_url)
