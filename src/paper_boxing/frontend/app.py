# SPDX-FileCopyrightText: 2026 Gary Frattarola <garyf@parkviewlab.ai>
#
# SPDX-License-Identifier: MIT OR Apache-2.0

"""The NiceGUI application: the handbook's ops endpoints and the entry point.

The pages (sign-in, sites, one site, tokens, users, account) are the frontend
worker's (docs/design.md sections 5 and 9). Every page is built inside its
`@ui.page` function for the connecting client; no UI element and no per-user
value lives at module level. This module keeps only the shared, stateless
pieces: the ops routes and `run()`.
"""

from __future__ import annotations

import time

from nicegui import app, ui

from paper_boxing.common.schema import Health
from paper_boxing.frontend.config import NAME, VERSION, FrontendConfig, load_config

_started_at = time.time()


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


@ui.page("/")
def index_page() -> None:
    """Placeholder until the frontend worker lands the pages."""
    ui.label("paper-boxing").classes("text-2xl")
    ui.label(f"frontend v{VERSION}: the pages are under construction.")


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
