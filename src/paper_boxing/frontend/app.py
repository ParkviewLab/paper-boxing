# SPDX-FileCopyrightText: 2026 Gary Frattarola <garyf@parkviewlab.ai>
#
# SPDX-License-Identifier: MIT OR Apache-2.0

"""The NiceGUI application: the handbook's ops endpoints, the download route,
`install()` and the entry point `run()`.

Every page is built inside its `ui.page` function for the connecting client
(`pages/`); no UI element and no per-user value lives at module level. The one
shared object is the stateless backend client that `backend.py` opens in the
app lifespan. `install()` registers everything on NiceGUI's app and is what
`__main__` calls before `run()`; the tests call it too, against a fake backend.
"""

from __future__ import annotations

import time
from collections.abc import Callable
from urllib.parse import quote

import httpx
from fastapi.responses import JSONResponse, Response
from nicegui import app, ui

from paper_boxing.common.client import BackendError
from paper_boxing.common.naming import path_name
from paper_boxing.common.schema import ErrorBody, ErrorCode, ErrorDetail, Health
from paper_boxing.frontend import auth, backend, pages
from paper_boxing.frontend.config import NAME, VERSION, FrontendConfig, load_config

_started_at = time.time()

DOWNLOAD_ROUTE = "/download/{slug}/{path:path}"


def _error(status: int, code: ErrorCode, message: str) -> JSONResponse:
    body = ErrorBody(error=ErrorDetail(code=code, message=message))
    return JSONResponse(body.model_dump(mode="json"), status_code=status)


def _register_routes() -> None:
    """The ops endpoints and the download route; idempotent, so a re-install after a test reset is clean."""
    for path in ("/health", "/admin/version", DOWNLOAD_ROUTE):
        app.remove_route(path)

    @app.get("/health", response_model=Health, tags=["health"])
    async def health() -> Health:
        return Health(ok=True, version=VERSION, uptime_seconds=time.time() - _started_at)

    @app.get("/admin/version", tags=["admin"])
    async def admin_version() -> dict[str, str]:
        cfg = load_config()
        return {
            "name": NAME,
            "version": VERSION,
            "backend_url": cfg.backend_url,
            "public_sites_url": cfg.public_sites_url,
        }

    @app.get(DOWNLOAD_ROUTE, tags=["files"])
    async def download(slug: str, path: str) -> Response:
        """Fetch one file from the backend with the caller's session and hand it to the browser as an attachment.

        The browser never holds the session token, so it cannot fetch from the
        backend itself; this route does it on its behalf. It answers errors in
        the contract's shape.
        """
        token = auth.token()
        if token is None:
            return _error(401, ErrorCode.UNAUTHORIZED, "sign in to download files")
        try:
            file = await backend.client().download_file(token, slug, path)
        except BackendError as e:
            code = ErrorCode(e.code) if e.code in ErrorCode.__members__.values() else ErrorCode.INTERNAL_ERROR
            return _error(e.status, code, e.message)
        name = path_name(file.path)
        ascii_name = name.encode("ascii", "replace").decode("ascii").replace('"', "'")
        return Response(
            content=file.content,
            media_type=file.content_type,
            headers={
                "Content-Disposition": f"attachment; filename=\"{ascii_name}\"; filename*=UTF-8''{quote(name)}",
                "Cache-Control": "no-store",
            },
        )


def install(
    cfg: FrontendConfig, *, http_client_factory: Callable[[], httpx.AsyncClient] | None = None
) -> None:
    """Register the frontend on NiceGUI's app: the shared client's lifespan, the middleware, the routes, the pages.

    `http_client_factory` replaces the httpx client that talks to `cfg.backend_url`;
    the tests pass one with an `httpx.ASGITransport` around the fake backend.
    """
    backend.configure(cfg, http_client_factory)
    auth.install_middleware()
    _register_routes()
    pages.register()


def run(cfg: FrontendConfig) -> None:
    """Start the UI server. `reload=False`: the image runs one process, and reload needs a file watcher."""
    ui.run(
        host=cfg.host,
        port=cfg.port,
        title="paper-boxing",
        storage_secret=cfg.storage_secret,
        reload=False,
        show=False,
        show_welcome_message=False,
    )
