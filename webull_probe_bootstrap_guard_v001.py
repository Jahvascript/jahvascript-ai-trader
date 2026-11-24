"""Fail-closed Webull SDK bootstrap request guard.

Offline policy only. No authentication, network, or trading.
"""

from webull_probe_destination_guard_v001 import (
    HOST,
    check_prepared_request,
    check_response,
)
from webull_probe_request_guard_v001 import (
    ProbeRequestGuard,
)

BOOTSTRAP_PATHS = {
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
            "WEBULL BOOTSTRAP BLOCKED: " + reason
        )


class BootstrapGuard:
    def __init__(self, classes):
        self.classes = classes
        self.budget = ProbeRequestGuard(classes)
        self.counts = {
            name: 0 for name in BOOTSTRAP_PATHS
        }

    def check_sdk(self, request):
        cls = type(request)
        name = cls.__name__

        require(
            name in BOOTSTRAP_PATHS
            and self.classes.get(name) is cls,
            "Unexpected SDK request class"
        )

        path, method = BOOTSTRAP_PATHS[name]

        require(
            request.get_action_name() == path,
            "Unexpected SDK action"
        )
        require(
            request.get_method() == method,
            "Unexpected HTTP method"
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
            "Raw request content forbidden"
        )
        require(
            request.get_body_params() in (None, {}),
            "Unexpected initialization body"
        )

        headers = request.get_headers()

        require(
            isinstance(headers, dict)
            and set(headers) == {"x-version"},
            "Unexpected unsigned SDK headers"
        )

        require(
            self.counts[name] < 2,
            "Per-operation request budget exhausted"
        )

        # Existing exact-class guard also enforces
        # the total initialization request budget.
        result = self.budget.permit(request)

        require(
            result == "SDK_INITIALIZATION",
            "Not an initialization request"
        )

        self.counts[name] += 1
        return name

    def check_prepared(self, prepared):
        path = check_prepared_request(prepared)

        matching = [
            method for expected_path, method
            in BOOTSTRAP_PATHS.values()
            if expected_path == path
        ]

        require(
            len(matching) == 1,
            "Not an authorized bootstrap endpoint"
        )

        require(
            prepared.method == matching[0],
            "Incorrect HTTP method"
        )

        body = prepared.body

        if prepared.method == "GET":
            require(
                body in (None, b"", ""),
                "GET bootstrap body forbidden"
            )
        else:
            require(
                body in (b"{}", "{}"),
                "Unexpected POST bootstrap body"
            )

        return path

    def check_result(self, response, original_url):
        return check_response(response, original_url)
