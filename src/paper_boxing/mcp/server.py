# SPDX-FileCopyrightText: 2026 Gary Frattarola <garyf@parkviewlab.ai>
#
# SPDX-License-Identifier: MIT OR Apache-2.0

"""FastAPI app construction, MCP server wiring and lifespan.

This is the transport plumbing in the handbook's shape: a low-level MCP
`Server` behind a stateless `StreamableHTTPSessionManager` mounted at `/mcp`
(POST only; GET and DELETE answer 405), transport security on, CORS limited to
the same origin allowlist, gzip, and the ops endpoints.

What is deliberately not here, because it is the MCP worker's (docs/design.md
sections 4a and 9): the `list_tools` and `call_tool` handlers, the per-request
token confirmation against the backend (`GET /api/v1/tokens/self`), and the
tool dispatch. The tool specifications they implement are in `tools.py`; the
bearer token of the HTTP request that carried a JSON-RPC message is reachable
inside a handler as `mcp.request_context.request.headers["authorization"]`.
"""

from __future__ import annotations

import contextlib
import logging
import time
from collections.abc import AsyncIterator

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.gzip import GZipMiddleware
from mcp.server import Server
from mcp.server.streamable_http_manager import StreamableHTTPSessionManager
from mcp.server.transport_security import TransportSecuritySettings
from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.routing import Route

from paper_boxing.common.schema import Health
from paper_boxing.mcp.config import NAME, VERSION, McpConfig, load_config

logger = logging.getLogger(__name__)
_started_at = time.time()

cfg: McpConfig = load_config()

# ---------------------------------------------------------------------------
# MCP server, mounted at /mcp by the FastAPI app below.

mcp = Server(NAME, version=VERSION)

session_manager = StreamableHTTPSessionManager(
    app=mcp,
    stateless=True,
    security_settings=TransportSecuritySettings(
        enable_dns_rebinding_protection=cfg.enable_transport_security,
        allowed_hosts=cfg.allowed_hosts,
        allowed_origins=cfg.allowed_origins,
    ),
)


class MCPASGIApp:
    """Raw ASGI3 endpoint (a class instance, so Starlette does not wrap it in request_response)."""

    async def __call__(self, scope, receive, send) -> None:
        await session_manager.handle_request(scope, receive, send)


mcp_asgi = MCPASGIApp()


async def legacy_sse(request: Request) -> JSONResponse:
    """The old HTTP+SSE path answers 405 naming /mcp, so a legacy client fails instead of hanging."""
    return JSONResponse(
        {
            "error": "method_not_allowed",
            "message": "This server speaks Streamable HTTP at /mcp (POST). There is no /sse transport.",
        },
        status_code=405,
        headers={"Allow": ""},
    )


# ---------------------------------------------------------------------------
# Lifespan: runs the MCP session manager (its run() may be entered only once).


@contextlib.asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    async with session_manager.run():
        logging.getLogger("uvicorn.error").info(
            "%s v%s ready (backend=%s, transport_security=%s)",
            NAME,
            VERSION,
            cfg.backend_url,
            cfg.enable_transport_security,
        )
        yield


# ---------------------------------------------------------------------------
# FastAPI app

app = FastAPI(
    title=NAME,
    version=VERSION,
    description="paper-boxing MCP server: tool calls go to /mcp over Streamable HTTP; /admin/* is read-only.",
    lifespan=lifespan,
    docs_url="/docs",
    redoc_url=None,
    openapi_url="/openapi.json",
)
app.add_middleware(
    CORSMiddleware,
    allow_origins=cfg.allowed_origins,  # the MCP transport's allowlist, never "*"
    allow_methods=["*"],
    allow_headers=["*"],
)
app.add_middleware(GZipMiddleware, minimum_size=256)
app.router.routes.append(Route("/mcp", endpoint=mcp_asgi, methods=["POST"]))
app.router.routes.append(Route("/sse", endpoint=legacy_sse, methods=["GET", "POST", "DELETE"]))


@app.get("/health", response_model=Health, tags=["health"])
async def health() -> Health:
    return Health(ok=True, version=VERSION, uptime_seconds=time.time() - _started_at)


@app.get("/admin/version", tags=["admin"])
async def admin_version() -> dict[str, object]:
    return {
        "name": NAME,
        "version": VERSION,
        "backend_url": cfg.backend_url,
        "public_sites_url": cfg.public_sites_url,
        "max_file_mb": cfg.max_file_mb,
        "transport_security": cfg.enable_transport_security,
        "allowed_hosts": cfg.allowed_hosts,
    }
