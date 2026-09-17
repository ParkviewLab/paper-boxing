# SPDX-FileCopyrightText: 2026 Gary Frattarola <garyf@parkviewlab.ai>
#
# SPDX-License-Identifier: MIT OR Apache-2.0

"""Environment helpers and the one place the version is derived.

Pure leaf module: it imports nothing from the package. Each component's
`config.py` imports from here and from nothing else in the package, so it
stays a leaf of its own subpackage. The version has exactly one source of
truth, `pyproject.toml`; `VERSION` is read from the installed package
metadata (see the handbook's releases.md), never from a literal.
"""

from __future__ import annotations

import os
from importlib.metadata import PackageNotFoundError, version

try:
    VERSION: str = version("paper-boxing")
except PackageNotFoundError:  # editable install before first build
    VERSION = "0.0.0+local"

# The bind address defaults to loopback in code; the images set HOST=0.0.0.0
# (a container must bind every interface to be reachable).
DEFAULT_HOST = "127.0.0.1"

# Published ports (docs/decisions.md, 2026-09-15). Each image listens on
# its own number inside the container as well.
FRONTEND_PORT = 35840
SITES_PORT = 35841
MCP_PORT = 35842
BACKEND_PORT = 35843

DEFAULT_PUBLIC_SITES_URL = f"http://127.0.0.1:{SITES_PORT}"
DEFAULT_BACKEND_URL = f"http://127.0.0.1:{BACKEND_PORT}"


def str_env(name: str, default: str) -> str:
    """A string variable; an empty value counts as unset."""
    raw = os.environ.get(name)
    return default if raw is None or raw.strip() == "" else raw.strip()


def optional_env(name: str) -> str | None:
    """A string variable that may be absent; an empty value counts as unset."""
    raw = os.environ.get(name)
    return None if raw is None or raw.strip() == "" else raw.strip()


def int_env(name: str, default: int) -> int:
    """An integer variable. A non-integer value is a configuration error and raises."""
    raw = os.environ.get(name)
    if raw is None or raw.strip() == "":
        return default
    try:
        return int(raw.strip())
    except ValueError as e:
        raise ValueError(f"{name} must be an integer, got {raw!r}") from e


def bool_env(name: str, *, default: bool) -> bool:
    """A boolean variable: 1/true/yes/on are true, 0/false/no/off are false (case-insensitive)."""
    raw = os.environ.get(name)
    if raw is None or raw.strip() == "":
        return default
    value = raw.strip().lower()
    if value in ("1", "true", "yes", "on"):
        return True
    if value in ("0", "false", "no", "off"):
        return False
    raise ValueError(f"{name} must be a boolean (1/0, true/false, yes/no, on/off), got {raw!r}")


def csv_env(name: str) -> list[str]:
    """A comma-separated list; items are trimmed and empty items dropped."""
    return [item.strip() for item in os.environ.get(name, "").split(",") if item.strip()]


def url_env(name: str, default: str) -> str:
    """A URL variable, with any trailing slash removed so paths can be appended."""
    return str_env(name, default).rstrip("/")
