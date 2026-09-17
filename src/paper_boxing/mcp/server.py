# SPDX-FileCopyrightText: 2026 Gary Frattarola <garyf@parkviewlab.ai>
#
# SPDX-License-Identifier: MIT OR Apache-2.0

"""FastAPI app construction, MCP server wiring and lifespan.

The transport plumbing in the handbook's shape: a low-level MCP `Server`
behind a stateless `StreamableHTTPSessionManager` mounted at `/mcp` (POST
only; GET and DELETE answer 405), transport security on, CORS limited to the
same origin allowlist, gzip, and the ops endpoints.

The ASGI callable at `/mcp` is `auth.TokenGate`, the three safeguards of
docs/architecture.md, "The MCP server": every request's bearer is confirmed with the
backend before the transport sees the request, nothing is kept between
requests, and only an agent token passes. The handlers below read that
confirmation from the HTTP request the SDK exposes as
`mcp.request_context.request`, list the tools the token's scope allows, and
run a call through `tools.dispatch` with the token forwarded to the backend
under `X-Paper-Boxing-Via: mcp`. Every failure comes back as a tool result
with `isError`, never as an exception the client cannot read, and every HTTP
error of this app (the gate's, `/sse`, a 404 or 405) has the contract's body.

One `httpx.AsyncClient` serves every request; the lifespan opens it through
`http_client_factory`, the one seam a test replaces to point the server at
the in-memory fake backend (`httpx.ASGITransport`).
"""

from __future__ import annotations

import contextlib
import logging
import time
from collections.abc import AsyncIterator, Callable
from typing import Any

import httpx
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.gzip import GZipMiddleware
from mcp import types
from mcp.server import Server
from mcp.server.streamable_http_manager import StreamableHTTPSessionManager
from mcp.server.transport_security import TransportSecuritySettings
from mcp.shared.exceptions import McpError
from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.routing import Route

from paper_boxing.common.client import BackendClient
from paper_boxing.common.errors import error_response, install_error_handlers
from paper_boxing.common.routes import VIA_MCP
from paper_boxing.common.schema import ErrorCode, Health
from paper_boxing.mcp import auth, tools
from paper_boxing.mcp.config import NAME, VERSION, McpConfig, load_config

logger = logging.getLogger(__name__)
_started_at = time.time()

cfg: McpConfig = load_config()

SPECS = tools.tool_specs(cfg.max_file_mb)

# ---------------------------------------------------------------------------
# The backend client: one httpx.AsyncClient for the process, opened in the lifespan.


def request_body_limit(max_file_bytes: int) -> int:
    """The largest request body /mcp accepts: a file at the cap, base64-encoded, inside its JSON-RPC envelope.

    The encoding may be line-wrapped at 76 columns (`base64.encodebytes`), which `upload_file`
    accepts, so each line's newline costs two bytes escaped in JSON; the envelope gets 64 KiB.
    """
    encoded = 4 * ((max_file_bytes + 2) // 3)
    lines = (encoded + 75) // 76
    return encoded + 2 * lines + 64 * 1024


def _real_http_client(config: McpConfig) -> httpx.AsyncClient:
    return httpx.AsyncClient(base_url=config.backend_url, timeout=httpx.Timeout(60.0, connect=5.0))


# Replaced by the tests with a client over `httpx.ASGITransport(app=create_fake_backend())`.
http_client_factory: Callable[[McpConfig], httpx.AsyncClient] = _real_http_client

_backend: BackendClient | None = None


def backend() -> BackendClient:
    """The process's backend client; only available while the lifespan runs."""
    if _backend is None:
        raise RuntimeError("the MCP server's lifespan has not started; no backend client is open")
    return _backend


# ---------------------------------------------------------------------------
# MCP server, mounted at /mcp by the FastAPI app below.

mcp = Server(NAME, version=VERSION)

security_settings = TransportSecuritySettings(
    enable_dns_rebinding_protection=cfg.enable_transport_security,
    allowed_hosts=cfg.allowed_hosts,
    allowed_origins=cfg.allowed_origins,
)

session_manager = StreamableHTTPSessionManager(
    app=mcp,
    stateless=True,
    security_settings=security_settings,
    # The SDK's default (4 MiB) would refuse a base64 upload above about 3 MiB.
    max_request_body_size=request_body_limit(cfg.max_file_bytes),
)


def _confirmed() -> auth.Confirmed:
    """The gate's confirmation for the request being handled; refuses if the gate did not run."""
    request: Request | None = mcp.request_context.request
    confirmed = auth.confirmed_from(request)
    if confirmed is None:
        raise tools.ToolError(
            ErrorCode.UNAUTHORIZED, "no confirmed agent token on this request; nothing was run"
        )
    return confirmed


@mcp.list_tools()
async def list_tools(request: types.ListToolsRequest) -> types.ListToolsResult:
    try:
        confirmed = _confirmed()
    except tools.ToolError as e:
        raise McpError(types.ErrorData(code=types.INVALID_REQUEST, message=str(e))) from e
    # A ListToolsResult merges into the SDK's schema cache, where a plain list would clear it to
    # this caller's scope; the cache serves validation, and authorization stays in the dispatch.
    return types.ListToolsResult(tools=tools.list_tools(SPECS, confirmed.token.scope))


@mcp.call_tool(validate_input=False)
async def call_tool(name: str, arguments: dict[str, Any]) -> dict[str, Any] | types.CallToolResult:
    # Arguments are validated by `tools.dispatch` against the tool's input model, so that a bad call
    # is refused in the contract's error shape; the SDK validates the structured output against the
    # tool's outputSchema afterwards.
    try:
        confirmed = _confirmed()
        token = confirmed.token
        logger.info("tool %s: token %s (%s) of %s", name, token.id, token.name, token.username)
        ctx = tools.ToolContext(backend=backend(), bearer=confirmed.bearer, max_file_bytes=cfg.max_file_bytes)
        return await tools.dispatch(SPECS, name, arguments, ctx, token.scope)
    except tools.ToolError as e:
        logger.info("tool %s refused: %s", name, e)
        return e.result()
    except Exception as e:  # every failure is a tool error the client can read
        logger.exception("tool %s failed", name)
        return tools.ToolError(ErrorCode.INTERNAL_ERROR, f"{type(e).__name__}: {e}").result()


# The raw ASGI3 endpoint at /mcp (a class instance, so Starlette does not wrap it in request_response).
mcp_asgi = auth.TokenGate(
    session_manager.handle_request,
    backend,
    security=security_settings,
    max_body_bytes=session_manager.max_request_body_size,
)


async def legacy_sse(request: Request) -> JSONResponse:
    """The old HTTP+SSE path answers 405 naming /mcp, so a legacy client fails instead of hanging."""
    return error_response(
        405,
        ErrorCode.METHOD_NOT_ALLOWED,
        "This server speaks Streamable HTTP at /mcp (POST). There is no /sse transport.",
        headers={"Allow": ""},
    )


# ---------------------------------------------------------------------------
# Lifespan: opens the backend client and runs the MCP session manager (its run() may be entered only once).


@contextlib.asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    global _backend
    http = http_client_factory(cfg)
    _backend = BackendClient(http, via=VIA_MCP)
    try:
        async with session_manager.run():
            logging.getLogger("uvicorn.error").info(
                "%s v%s ready (backend=%s, max_file_mb=%s, transport_security=%s)",
                NAME,
                VERSION,
                cfg.backend_url,
                cfg.max_file_mb,
                cfg.enable_transport_security,
            )
            yield
    finally:
        _backend = None
        await http.aclose()


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
install_error_handlers(app)  # a 404 or 405 from the framework has the contract's body too
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
