# SPDX-FileCopyrightText: 2026 Gary Frattarola <garyf@parkviewlab.ai>
#
# SPDX-License-Identifier: MIT OR Apache-2.0

"""FastAPI app construction and lifespan for the backend.

`create_app(config)` builds one backend over one data directory: the
handbook's standard endpoints (`/health`, `/admin/version`, `/docs`), the
error-body convention from docs/api.md, and the REST routes of
`paper_boxing.backend.routes`. The lifespan lays out the data directory,
opens the database, creates the first account from the admin variables when
no account exists, and closes the database at shutdown. The module-level
`app` is the one uvicorn serves, built from the environment; tests build
their own over a temporary directory, optionally with a clock they can
advance and a cheaper password hasher.
"""

from __future__ import annotations

import contextlib
import logging
import time
from collections.abc import AsyncIterator

import argon2
from fastapi import FastAPI

from paper_boxing.backend.accounts import Accounts
from paper_boxing.backend.clock import Clock
from paper_boxing.backend.config import NAME, VERSION, BackendConfig, load_config
from paper_boxing.backend.context import Backend
from paper_boxing.backend.db import Database
from paper_boxing.backend.routes import register_routes
from paper_boxing.backend.storage import Storage
from paper_boxing.common.errors import install_error_handlers
from paper_boxing.common.schema import Health

logger = logging.getLogger(__name__)

cfg: BackendConfig = load_config()


def ensure_data_layout(config: BackendConfig) -> None:
    """Create the data directory tree the design fixes: sites/ (served by nginx) and staging/."""
    config.sites_dir.mkdir(parents=True, exist_ok=True)
    config.staging_dir.mkdir(parents=True, exist_ok=True)


def create_app(
    config: BackendConfig,
    *,
    clock: Clock | None = None,
    password_hasher: argon2.PasswordHasher | None = None,
) -> FastAPI:
    """A backend over `config.data_dir`. `clock` and `password_hasher` exist for tests."""
    started_at = time.time()
    app_clock = clock or Clock()

    @contextlib.asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        ensure_data_layout(config)
        storage = Storage(config.sites_dir, config.staging_dir, max_upload_bytes=config.max_upload_bytes)
        storage.clean_staging()
        db = Database.open(config.database_path)
        try:
            accounts = Accounts(db, app_clock, session_days=config.session_days, hasher=password_hasher)
            accounts.bootstrap(config.admin_username, config.admin_password)
            purged = accounts.purge_dead_sessions()
            if purged:
                logger.info("purged %d expired or revoked sessions", purged)
            app.state.backend = Backend(
                config=config, clock=app_clock, db=db, accounts=accounts, storage=storage
            )
            logging.getLogger("uvicorn.error").info(
                "%s v%s ready (data_dir=%s, public_sites_url=%s, max_upload_mb=%d, session_days=%d)",
                NAME,
                VERSION,
                config.data_dir,
                config.public_sites_url,
                config.max_upload_mb,
                config.session_days,
            )
            yield
        finally:
            db.close()

    app = FastAPI(
        title=NAME,
        version=VERSION,
        description="paper-boxing backend: the REST API for sites, files, accounts and tokens.",
        lifespan=lifespan,
        docs_url="/docs",
        redoc_url=None,
        openapi_url="/openapi.json",
    )
    app.state.clock = app_clock
    install_error_handlers(app)

    @app.get("/health", response_model=Health, tags=["health"])
    async def health() -> Health:
        return Health(ok=True, version=VERSION, uptime_seconds=time.time() - started_at)

    @app.get("/admin/version", tags=["admin"])
    async def admin_version() -> dict[str, str]:
        return {
            "name": NAME,
            "version": VERSION,
            "data_dir": str(config.data_dir),
            "public_sites_url": config.public_sites_url,
        }

    register_routes(app)
    return app


app = create_app(cfg)
