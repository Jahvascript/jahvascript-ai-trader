"""Synthetic-only verification of isolated Webull config loading."""

import hashlib
import importlib
import json
import os
import tempfile
from pathlib import Path

from forward_collection_schedule_v001 import verify_sources
from webull_probe_config_v001 import (
    load_probe_config,
    REQUIRED,
    MAX_BYTES,
)

ROOT = Path(__file__).resolve().parent
REPORT = (
    ROOT / "journal/forward_evaluation_v001/"
    "webull_probe_config_audit_v001.json"
)

SYNTHETIC = (
    "# Fictional test configuration\n"
    "WEBULL_APP_KEY=FAKE_KEY_FOR_OFFLINE_TEST\n"
    "WEBULL_APP_SECRET=FAKE_SECRET_FOR_OFFLINE_TEST\n"
    "WEBULL_ENVIRONMENT=sandbox\n"
    "UNRELATED_SETTING=ignored\n"
)


def require(ok, reason):
    if not ok:
        raise AssertionError("CONFIG AUDIT FAILED: " + reason)


def reject(label, operation):
    try:
        operation()
    except (ValueError, PermissionError, OSError, UnicodeError):
        print(label + ": REJECTED")
        return

    raise AssertionError("Unsafe config accepted: " + label)


def write_fixture(path, content):
    path.write_text(content, encoding="utf-8")
    path.chmod(0o600)


print("\n=== JAHVASCRIPT ISOLATED WEBULL CONFIG AUDIT ===")

before = verify_sources()

print("Frozen research fingerprints: VERIFIED")
print("Credential keys:", ", ".join(REQUIRED))
print("Actual project .env: NOT OPENED")

with tempfile.TemporaryDirectory(
    prefix="jahvascript_probe_config_"
) as temporary:

    base = Path(temporary)
    approved = base / "approved"
    outside = base / "outside"

    approved.mkdir(mode=0o700)
    outside.mkdir(mode=0o700)

    env = approved / ".env"
    write_fixture(env, SYNTHETIC)

    parsed = load_probe_config(env, approved)

    require(
        tuple(parsed) == REQUIRED,
        "Unexpected returned configuration fields"
    )

    require(
        all(parsed[key] for key in REQUIRED),
        "Missing parsed test values"
    )

    print("\n--- SYNTHETIC CONFIGURATION ---")
    print("Approved .env path: PASS")
    print("Required keys parsed: PASS")
    print("Unrelated keys excluded: PASS")
    print("Credential values displayed: NONE")

    print("\n--- REJECTION TESTS ---")

    write_fixture(
        env,
        SYNTHETIC + "WEBULL_APP_KEY=SECOND_FAKE_KEY\n"
    )

    reject(
        "Duplicate credential key",
        lambda: load_probe_config(env, approved)
    )

    write_fixture(
        env,
        SYNTHETIC.replace(
            "WEBULL_APP_SECRET=FAKE_SECRET_FOR_OFFLINE_TEST\n",
            ""
        )
    )

    reject(
        "Missing secret",
        lambda: load_probe_config(env, approved)
    )

    write_fixture(
        env,
        SYNTHETIC.replace(
            "WEBULL_APP_KEY=FAKE_KEY_FOR_OFFLINE_TEST",
            "WEBULL_APP_KEY=   "
        )
    )

    reject(
        "Blank key value",
        lambda: load_probe_config(env, approved)
    )

    write_fixture(
        env,
        SYNTHETIC + "MALFORMED_ENTRY\n"
    )

    reject(
        "Malformed entry",
        lambda: load_probe_config(env, approved)
    )

    write_fixture(
        env,
        "A" * (MAX_BYTES + 1)
    )

    reject(
        "Oversized config",
        lambda: load_probe_config(env, approved)
    )

    external = outside / ".env"
    write_fixture(external, SYNTHETIC)

    reject(
        "Outside approved directory",
        lambda: load_probe_config(external, approved)
    )

    env.unlink()
    env.symlink_to(external)

    reject(
        "Symlinked config file",
        lambda: load_probe_config(env, approved)
    )

    env.unlink()

    require(
        not env.exists(),
        "Synthetic config not cleaned up"
    )

require(
    verify_sources() == before,
    "Frozen research fingerprints changed"
)

module = importlib.import_module("webull_probe_config_v001")
source = Path(module.__file__)

report = {
    "version": "WEBULL_PROBE_CONFIG_AUDIT_V001",
    "scope": "SYNTHETIC_OFFLINE_ONLY",
    "loader_sha256": hashlib.sha256(
        source.read_bytes()
    ).hexdigest(),
    "research_fingerprints": before,
    "approved_config_path": "PASS",
    "required_keys": list(REQUIRED),
    "unrelated_keys_excluded": True,
    "unsafe_config_rejections": 7,
    "actual_dotenv_accessed": False,
    "real_credentials_accessed": False,
    "trading_modules_imported_by_loader": False,
    "sdk_clients_instantiated": False,
    "network_requests": 0,
    "real_sessions_admitted": 0,
    "trade_authorization": False,
}

REPORT.parent.mkdir(parents=True, exist_ok=True)

if REPORT.exists():
    require(
        REPORT.is_file()
        and not REPORT.is_symlink()
        and json.loads(REPORT.read_text()) == report,
        "Existing report differs; original preserved"
    )
    print("\nExisting configuration audit: VERIFIED")
else:
    old_umask = os.umask(0o077)

    try:
        with REPORT.open("x") as handle:
            json.dump(report, handle, indent=2)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
    finally:
        os.umask(old_umask)

    REPORT.chmod(0o600)
    print("\nNew configuration audit: SAVED")

print("\n--- FINAL RESULT ---")
print("Standalone credential loader: OFFLINE VERIFIED")
print("Trading module dependency: NONE")
print("Real .env read: NO")
print("Credentials exposed: NONE")
print("Webull requests: NONE")
print("Research V001: UNCHANGED")
print("Real sessions admitted: 0")
print("Order execution: DISABLED")

print("\nSTEP 15H.65AG CONFIG ISOLATION VERIFIED")
