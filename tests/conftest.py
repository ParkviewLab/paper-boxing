# SPDX-FileCopyrightText: 2026 Gary Frattarola <garyf@parkviewlab.ai>
#
# SPDX-License-Identifier: MIT OR Apache-2.0

"""Shared fixtures.

The MCP transport's `StreamableHTTPSessionManager` refuses a second `run()`,
and the FastAPI lifespan calls `run()`; so exactly one session-scoped
`TestClient` drives the MCP app for the whole session, and every module that
touches `/mcp` reuses it. Environment variables are set before the server
module is imported, because `server.py` reads its config at import time; the
reload guard covers the case where another module imported it first.

The backend and frontend apps have no such constraint, but the same shape is
used for symmetry. Data lives in `tmp_path_factory` directories only, except
NiceGUI's own storage, which its test plugin points at a temporary directory
of its own before the first test.

The frontend's page fixtures live in `tests/frontend_fixtures.py`, loaded
through `pytest_plugins` at the end of this file beside NiceGUI's user plugin.
"""

from __future__ import annotations

import importlib
import os
import sys
from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient


@pytest.fixture(scope="session")
def backend_client(tmp_path_factory: pytest.TempPathFactory) -> Iterator[TestClient]:
    data_dir = tmp_path_factory.mktemp("paper-boxing-data")
    os.environ["PAPER_BOXING_DATA_DIR"] = str(data_dir)
    if "paper_boxing.backend.app" in sys.modules:
        importlib.reload(sys.modules["paper_boxing.backend.app"])
    from paper_boxing.backend.app import app

    with TestClient(app, base_url="http://localhost") as client:
        yield client


@pytest.fixture(scope="session")
def frontend_client() -> Iterator[TestClient]:
    os.environ["PAPER_BOXING_STORAGE_SECRET"] = "test-secret"
    from nicegui import app

    from paper_boxing.frontend.app import install
    from paper_boxing.frontend.config import load_config

    install(load_config())  # the routes on NiceGUI's app
    # No lifespan: NiceGUI's startup wants a running ui.run(); the ops routes need none of it.
    yield TestClient(app, base_url="http://localhost")


@pytest.fixture(scope="session")
def mcp_client() -> Iterator[TestClient]:
    os.environ["PAPER_BOXING_MCP_ENABLE_TRANSPORT_SECURITY"] = "true"
    os.environ.pop("PAPER_BOXING_MCP_ALLOWED_HOSTS", None)
    os.environ.pop("PAPER_BOXING_MCP_ALLOWED_ORIGINS", None)
    if "paper_boxing.mcp.server" in sys.modules:
        importlib.reload(sys.modules["paper_boxing.mcp.server"])
    from paper_boxing.mcp.server import app

    # base_url -> Host: localhost, which the transport's allowlist accepts
    # (the TestClient default "testserver" would be rejected with 421).
    with TestClient(app, base_url="http://localhost") as client:
        yield client


# NiceGUI's user simulation (user_plugin, not plugin, which imports selenium) and the frontend's fixtures.
pytest_plugins = ["nicegui.testing.user_plugin", "tests.frontend_fixtures"]
