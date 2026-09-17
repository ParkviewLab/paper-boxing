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

The MCP server is pointed at an in-memory fake backend (`create_fake_backend`)
through `server.http_client_factory`, the one seam the server exposes for it;
`fake_state` is that backend's state, for issuing tokens and reading the
request record. Both live for the session, like the client, so tests create
their own sites under distinct names.

The backend and frontend apps have no such constraint, but the same shape is
used for symmetry. Data lives in `tmp_path_factory` directories only.
"""

from __future__ import annotations

import importlib
import os
import sys
from collections.abc import Iterator

import httpx
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from paper_boxing.common.fake_backend import FakeState, create_fake_backend

ADMIN = ("admin", "admin-password")


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

    import paper_boxing.frontend.app  # noqa: F401  (registers the routes on NiceGUI's app)

    # No lifespan: NiceGUI's startup wants a running ui.run(); the ops routes need none of it.
    yield TestClient(app, base_url="http://localhost")


@pytest.fixture(scope="session")
def fake_backend_app() -> FastAPI:
    """The in-memory backend the MCP server under test calls; one for the session."""
    return create_fake_backend(admin=ADMIN)


@pytest.fixture(scope="session")
def fake_state(fake_backend_app: FastAPI) -> FakeState:
    return fake_backend_app.state.fake


@pytest.fixture(scope="session")
def mcp_client(fake_backend_app: FastAPI) -> Iterator[TestClient]:
    os.environ["PAPER_BOXING_MCP_ENABLE_TRANSPORT_SECURITY"] = "true"
    os.environ.pop("PAPER_BOXING_MCP_ALLOWED_HOSTS", None)
    os.environ.pop("PAPER_BOXING_MCP_ALLOWED_ORIGINS", None)
    os.environ.pop("PAPER_BOXING_MCP_MAX_FILE_MB", None)
    if "paper_boxing.mcp.server" in sys.modules:
        importlib.reload(sys.modules["paper_boxing.mcp.server"])
    from paper_boxing.mcp import server

    server.http_client_factory = lambda config: httpx.AsyncClient(
        transport=httpx.ASGITransport(app=fake_backend_app), base_url=config.backend_url
    )
    # base_url -> Host: localhost, which the transport's allowlist accepts
    # (the TestClient default "testserver" would be rejected with 421).
    with TestClient(server.app, base_url="http://localhost") as client:
        yield client
