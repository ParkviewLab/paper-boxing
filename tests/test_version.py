# SPDX-FileCopyrightText: 2026 Gary Frattarola <garyf@parkviewlab.ai>
#
# SPDX-License-Identifier: MIT OR Apache-2.0

"""The version has one source of truth (pyproject.toml) and is derived at runtime."""

from __future__ import annotations

import tomllib
from importlib.metadata import version
from pathlib import Path

import paper_boxing
from paper_boxing.backend import config as backend_config
from paper_boxing.common.config import VERSION
from paper_boxing.frontend import config as frontend_config
from paper_boxing.mcp import config as mcp_config

ROOT = Path(__file__).resolve().parent.parent


def test_version_is_read_from_package_metadata() -> None:
    assert version("paper-boxing") == VERSION
    assert paper_boxing.__version__ == VERSION


def test_version_matches_pyproject() -> None:
    data = tomllib.loads((ROOT / "pyproject.toml").read_text())
    assert data["project"]["version"] == VERSION


def test_every_component_reports_the_same_version() -> None:
    assert backend_config.VERSION == frontend_config.VERSION == mcp_config.VERSION == VERSION


def test_no_literal_copy_of_the_version_in_the_source() -> None:
    """Nothing in src/ hard-codes the version string; only the metadata lookup carries it."""
    literal = VERSION.split("+")[0]
    for path in (ROOT / "src").rglob("*.py"):
        assert f'"{literal}"' not in path.read_text(), path
