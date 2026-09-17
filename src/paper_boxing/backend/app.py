# SPDX-FileCopyrightText: 2026 Gary Frattarola <garyf@parkviewlab.ai>
#
# SPDX-License-Identifier: MIT OR Apache-2.0

"""FastAPI app construction and lifespan for the backend.

The scaffold provides the handbook's standard endpoints (`/health`,
`/admin/version`, `/docs`), the error-body convention from docs/api.md, and
the data-directory layout. The REST routes under /api/v1 are the backend
worker's (docs/design.md section 9): they go in `routes.py` and follow the
route table in `paper_boxing.common.routes`.
"""

from __future__ import annotations

import contextlib
import logging
import time
from collections.abc import AsyncIterator

from fastapi import FastAPI

from paper_boxing.backend.config import NAME, VERSION, BackendConfig, load_config
from paper_boxing.common.errors import install_error_handlers
from paper_boxing.common.schema import Health

logger = logging.getLogger(__name__)
_started_at = time.time()

cfg: BackendConfig = load_config()


def ensure_data_layout(config: BackendConfig) -> None:
    """Create the data directory tree the design fixes: sites/ (served by nginx) and staging/."""
    config.sites_dir.mkdir(parents=True, exist_ok=True)
    config.staging_dir.mkdir(parents=True, exist_ok=True)


@contextlib.asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    ensure_data_layout(cfg)
    logging.getLogger("uvicorn.error").info(
        "%s v%s ready (data_dir=%s, public_sites_url=%s)", NAME, VERSION, cfg.data_dir, cfg.public_sites_url
    )
    yield


app = FastAPI(
    title=NAME,
    version=VERSION,
    description="paper-boxing backend: the REST API for sites, files, accounts and tokens.",
    lifespan=lifespan,
    docs_url="/docs",
    redoc_url=None,
    openapi_url="/openapi.json",
)
install_error_handlers(app)


@app.get("/health", response_model=Health, tags=["health"])
async def health() -> Health:
    return Health(ok=True, version=VERSION, uptime_seconds=time.time() - _started_at)


@app.get("/admin/version", tags=["admin"])
async def admin_version() -> dict[str, str]:
    return {
        "name": NAME,
        "version": VERSION,
        "data_dir": str(cfg.data_dir),
        "public_sites_url": cfg.public_sites_url,
    }
