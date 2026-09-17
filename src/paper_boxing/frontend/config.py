# SPDX-FileCopyrightText: 2026 Gary Frattarola <garyf@parkviewlab.ai>
#
# SPDX-License-Identifier: MIT OR Apache-2.0

"""Frontend configuration. A leaf of the frontend subpackage: it imports only
`paper_boxing.common.config` (itself a pure leaf).

`PAPER_BOXING_STORAGE_SECRET` has no default: NiceGUI signs its per-user
storage cookie with it, so the frontend refuses to start without one (see
`__main__.py`) rather than run with a guessable key.
"""

from __future__ import annotations

from dataclasses import dataclass

from paper_boxing.common.config import (
    DEFAULT_BACKEND_URL,
    DEFAULT_HOST,
    DEFAULT_PUBLIC_SITES_URL,
    FRONTEND_PORT,
    VERSION,
    int_env,
    optional_env,
    str_env,
    url_env,
)

__all__ = ["VERSION", "FrontendConfig", "load_config"]

NAME = "paper-boxing-frontend"


@dataclass(frozen=True)
class FrontendConfig:
    host: str
    port: int
    backend_url: str
    public_sites_url: str
    storage_secret: str | None


def load_config() -> FrontendConfig:
    return FrontendConfig(
        host=str_env("HOST", DEFAULT_HOST),
        port=int_env("PORT", FRONTEND_PORT),
        backend_url=url_env("PAPER_BOXING_BACKEND_URL", DEFAULT_BACKEND_URL),
        public_sites_url=url_env("PAPER_BOXING_PUBLIC_SITES_URL", DEFAULT_PUBLIC_SITES_URL),
        storage_secret=optional_env("PAPER_BOXING_STORAGE_SECRET"),
    )
