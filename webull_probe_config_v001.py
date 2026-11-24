"""Isolated, read-only configuration loader for Webull schema probes."""

import os
import re
import stat
from pathlib import Path

REQUIRED = (
    "WEBULL_APP_KEY",
    "WEBULL_APP_SECRET",
    "WEBULL_ENVIRONMENT",
)

MAX_BYTES = 65536
KEY_PATTERN = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


def load_probe_config(config_path, approved_directory):
    """Read exactly the approved .env file; never print credentials."""

    root = Path(approved_directory).absolute()
    path = Path(config_path).absolute()

    if root.is_symlink() or not root.is_dir():
        raise PermissionError("Unapproved config directory")

    if path != root / ".env":
        raise PermissionError("Config path outside approved location")

    if path.is_symlink():
        raise PermissionError("Symlinked config file forbidden")

    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)

    # Opening with O_NOFOLLOW protects against symlink replacement
    # between the path check and opening the file.
    fd = os.open(path, flags)

    try:
        metadata = os.fstat(fd)

        if not stat.S_ISREG(metadata.st_mode):
            raise PermissionError("Config must be a regular file")

        if metadata.st_size > MAX_BYTES:
            raise ValueError("Config file exceeds size limit")

        with os.fdopen(fd, "rb", closefd=False) as handle:
            raw = handle.read(MAX_BYTES + 1)

        if len(raw) > MAX_BYTES:
            raise ValueError("Config file exceeds size limit")

        content = raw.decode("utf-8")
        parsed = {}

        for line in content.splitlines():
            stripped = line.strip()

            if not stripped or stripped.startswith("#"):
                continue

            if "=" not in line:
                raise ValueError("Malformed configuration entry")

            key, value = line.split("=", 1)
            key = key.strip()
            value = value.strip()

            if not KEY_PATTERN.fullmatch(key):
                raise ValueError("Invalid configuration key")

            if key in parsed:
                raise ValueError("Duplicate configuration key")

            parsed[key] = value

        for key in REQUIRED:
            if not parsed.get(key):
                raise ValueError(
                    "Missing required configuration key: " + key
                )

        # Return only fields required by this probe.
        return {key: parsed[key] for key in REQUIRED}

    finally:
        os.close(fd)
