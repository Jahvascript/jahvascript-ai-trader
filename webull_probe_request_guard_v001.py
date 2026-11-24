"""Fail-closed Webull schema-probe request policy.

Offline preparation only. This module does not provide
network access, authentication, or trade execution.
"""

import ast
import importlib
import importlib.metadata
from pathlib import Path

SDK_PACKAGE = "webull-openapi-python-sdk"
SDK_VERSION = "3.0.3"

BOOTSTRAP_NAMES = (
    "GetConfigRequest",
    "CheckTokenRequest",
    "CreateTokenRequest",
    "RefreshTokenRequest",
)

MARKET_DATA_NAME = "BatchHistoricalBarsRequest"

ALLOWED_NAMES = BOOTSTRAP_NAMES + (MARKET_DATA_NAME,)


def load_request_classes():
    """Resolve actual installed SDK classes, not name lookalikes."""

    dist = importlib.metadata.distribution(SDK_PACKAGE)

    if dist.version != SDK_VERSION:
        raise RuntimeError(
            "SDK version changed; request policy needs review"
        )

    candidates = {name: [] for name in ALLOWED_NAMES}

    for item in dist.files or []:
        relative = str(item)

        if not (
            relative.startswith("webull/")
            and relative.endswith(".py")
        ):
            continue

        path = Path(dist.locate_file(item))

        if not path.is_file():
            continue

        try:
            tree = ast.parse(
                path.read_text(encoding="utf-8")
            )
        except (SyntaxError, UnicodeError):
            continue

        for node in tree.body:
            if (
                isinstance(node, ast.ClassDef)
                and node.name in candidates
            ):
                module_name = relative[:-3].replace("/", ".")
                candidates[node.name].append(
                    (module_name, node.name)
                )

    resolved = {}

    for name, matches in candidates.items():
        if len(matches) != 1:
            raise RuntimeError(
                f"Expected one SDK definition of {name}; "
                f"found {len(matches)}"
            )

        module_name, class_name = matches[0]
        module = importlib.import_module(module_name)
        cls = getattr(module, class_name)

        if (
            not isinstance(cls, type)
            or cls.__module__ != module_name
        ):
            raise RuntimeError(
                "Unexpected SDK class identity: " + name
            )

        resolved[name] = cls

    return resolved


class ProbeRequestGuard:
    """Permits specific SDK request classes with strict budgets."""

    def __init__(self, classes):
        self.classes = classes
        self.allowed = frozenset(classes.values())
        self.bootstrap_calls = 0
        self.historical_calls = 0

    def permit_type(self, request_type):
        # Exact class identity, not the class name,
        # prevents an unrelated type from being accepted.
        if request_type not in self.allowed:
            raise PermissionError(
                "UNAPPROVED WEBULL SDK REQUEST BLOCKED"
            )

        if request_type is self.classes[MARKET_DATA_NAME]:
            if self.historical_calls >= 1:
                raise PermissionError(
                    "HISTORICAL REQUEST LIMIT EXCEEDED"
                )
            self.historical_calls += 1
            return "HISTORICAL_MARKET_DATA"

        if self.bootstrap_calls >= 8:
            raise PermissionError(
                "INITIALIZATION REQUEST LIMIT EXCEEDED"
            )

        self.bootstrap_calls += 1
        return "SDK_INITIALIZATION"

    def permit(self, request):
        return self.permit_type(type(request))
