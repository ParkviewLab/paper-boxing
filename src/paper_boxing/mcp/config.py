# SPDX-FileCopyrightText: 2026 Gary Frattarola <garyf@parkviewlab.ai>
#
# SPDX-License-Identifier: MIT OR Apache-2.0

"""MCP server configuration. A leaf of the mcp subpackage: it imports only
`paper_boxing.common.config` (itself a pure leaf).

Transport security (DNS-rebinding protection: a Host and Origin allowlist)
follows ebony-enriching. It is on by default with a loopback allowlist; a
deployment adds its own host (`<host>:35842`) to
`PAPER_BOXING_MCP_ALLOWED_HOSTS`. `HOST` stays env-driven because the image
must bind 0.0.0.0; the allowlist protects independently of the bind address.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from paper_boxing.common.config import (
    DEFAULT_BACKEND_URL,
    DEFAULT_HOST,
    DEFAULT_PUBLIC_SITES_URL,
    MCP_PORT,
    VERSION,
    bool_env,
    csv_env,
    int_env,
    str_env,
    url_env,
)

__all__ = ["VERSION", "McpConfig", "load_config"]

NAME = "paper-boxing-mcp"


def default_allowed_hosts() -> list[str]:
    return ["localhost", "127.0.0.1", "[::1]", "localhost:*", "127.0.0.1:*", "[::1]:*"]


def default_allowed_origins(port: int) -> list[str]:
    return [
        f"http://localhost:{port}",
        f"http://127.0.0.1:{port}",
        "http://localhost",
        "http://127.0.0.1",
    ]


@dataclass(frozen=True)
class McpConfig:
    host: str
    port: int
    backend_url: str
    public_sites_url: str
    max_file_mb: int
    enable_transport_security: bool
    allowed_hosts: list[str] = field(default_factory=default_allowed_hosts)
    allowed_origins: list[str] = field(default_factory=list)

    @property
    def max_file_bytes(self) -> int:
        return self.max_file_mb * 1024 * 1024


def load_config() -> McpConfig:
    port = int_env("PORT", MCP_PORT)
    return McpConfig(
        host=str_env("HOST", DEFAULT_HOST),
        port=port,
        backend_url=url_env("PAPER_BOXING_BACKEND_URL", DEFAULT_BACKEND_URL),
        public_sites_url=url_env("PAPER_BOXING_PUBLIC_SITES_URL", DEFAULT_PUBLIC_SITES_URL),
        max_file_mb=int_env("PAPER_BOXING_MCP_MAX_FILE_MB", 8),
        enable_transport_security=bool_env("PAPER_BOXING_MCP_ENABLE_TRANSPORT_SECURITY", default=True),
        allowed_hosts=csv_env("PAPER_BOXING_MCP_ALLOWED_HOSTS") or default_allowed_hosts(),
        allowed_origins=csv_env("PAPER_BOXING_MCP_ALLOWED_ORIGINS") or default_allowed_origins(port),
    )
