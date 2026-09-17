# SPDX-FileCopyrightText: 2026 Gary Frattarola <garyf@parkviewlab.ai>
#
# SPDX-License-Identifier: MIT OR Apache-2.0

"""Backend configuration. A leaf of the backend subpackage: it imports only
`paper_boxing.common.config` (itself a pure leaf) and reads the environment
with plain `os.environ` helpers; no pydantic-settings.

Every variable is documented in the README's Configuration table.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from paper_boxing.common.config import (
    BACKEND_PORT,
    DEFAULT_HOST,
    DEFAULT_PUBLIC_SITES_URL,
    VERSION,
    int_env,
    optional_env,
    str_env,
    url_env,
)

__all__ = ["VERSION", "BackendConfig", "load_config"]

NAME = "paper-boxing-backend"


@dataclass(frozen=True)
class BackendConfig:
    """Runtime configuration, built once at startup from the environment."""

    host: str
    port: int
    data_dir: Path
    public_sites_url: str
    admin_username: str | None
    admin_password: str | None
    max_upload_mb: int
    max_batch_files: int
    session_days: int

    @property
    def sites_dir(self) -> Path:
        return self.data_dir / "sites"

    @property
    def staging_dir(self) -> Path:
        return self.data_dir / "staging"

    @property
    def database_path(self) -> Path:
        return self.data_dir / "paper-boxing.sqlite3"

    @property
    def max_upload_bytes(self) -> int:
        return self.max_upload_mb * 1024 * 1024


def load_config() -> BackendConfig:
    """Read the backend's variables. `HOST` defaults to loopback in code; the image sets 0.0.0.0."""
    return BackendConfig(
        host=str_env("HOST", DEFAULT_HOST),
        port=int_env("PORT", BACKEND_PORT),
        data_dir=Path(str_env("PAPER_BOXING_DATA_DIR", "./data")).expanduser().resolve(),
        public_sites_url=url_env("PAPER_BOXING_PUBLIC_SITES_URL", DEFAULT_PUBLIC_SITES_URL),
        admin_username=optional_env("PAPER_BOXING_ADMIN_USERNAME"),
        admin_password=optional_env("PAPER_BOXING_ADMIN_PASSWORD"),
        max_upload_mb=int_env("PAPER_BOXING_MAX_UPLOAD_MB", 200),
        max_batch_files=int_env("PAPER_BOXING_MAX_BATCH_FILES", 500),
        session_days=int_env("PAPER_BOXING_SESSION_DAYS", 14),
    )
