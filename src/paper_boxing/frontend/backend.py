# SPDX-FileCopyrightText: 2026 Gary Frattarola <garyf@parkviewlab.ai>
#
# SPDX-License-Identifier: MIT OR Apache-2.0

"""The one shared object of the frontend: a stateless `httpx.AsyncClient`
wrapped in `BackendClient`, created at startup and closed at shutdown
(docs/architecture.md, "The frontend").

Nothing per user lives here. Every call passes the caller's session token
explicitly, so one client serves every signed-in person at once, and a test
swaps the transport for an in-process fake backend through `configure()`.
"""

from __future__ import annotations

from collections.abc import Callable

import httpx
from nicegui import app

from paper_boxing.common.client import BackendClient
from paper_boxing.frontend.config import FrontendConfig

# A short connect, a long read and write: a large upload over a slow link must not be reported
# as an unreachable backend after it has in fact succeeded (ten minutes suits the 200 MB cap).
TIMEOUT = httpx.Timeout(connect=10.0, read=600.0, write=600.0, pool=10.0)

_config: FrontendConfig | None = None
_factory: Callable[[], httpx.AsyncClient] | None = None
_client: BackendClient | None = None


def default_http_client(cfg: FrontendConfig) -> httpx.AsyncClient:
    """The httpx client that talks to the backend at `cfg.backend_url`."""
    return httpx.AsyncClient(base_url=cfg.backend_url, timeout=TIMEOUT)


def configure(
    cfg: FrontendConfig, http_client_factory: Callable[[], httpx.AsyncClient] | None = None
) -> None:
    """Remember the configuration and register the lifespan handlers that open and close the client.

    `http_client_factory` builds the raw httpx client; the default connects to
    `cfg.backend_url`, and a test passes one with an `httpx.ASGITransport`.
    """
    global _config, _factory
    _config = cfg
    _factory = http_client_factory or (lambda: default_http_client(cfg))
    app.on_startup(_open)
    app.on_shutdown(_close)


def config() -> FrontendConfig:
    if _config is None:
        raise RuntimeError("the frontend is not configured; call install() first")
    return _config


def client() -> BackendClient:
    """The shared client; valid between startup and shutdown."""
    if _client is None:
        raise RuntimeError("the backend client is not open; the app has not started")
    return _client


async def _open() -> None:
    global _client
    if _client is None:
        assert _factory is not None
        _client = BackendClient(_factory())


async def _close() -> None:
    global _client
    if _client is not None:
        await _client.aclose()
        _client = None
