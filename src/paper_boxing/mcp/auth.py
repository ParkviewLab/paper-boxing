# SPDX-FileCopyrightText: 2026 Gary Frattarola <garyf@parkviewlab.ai>
#
# SPDX-License-Identifier: MIT OR Apache-2.0

"""The three safeguards against token passthrough (docs/design.md section 4a).

`TokenGate` is an ASGI layer in front of the Streamable-HTTP transport at
`/mcp`. On every request it takes the bearer from the HTTP request, confirms
it with the backend (`GET /api/v1/tokens/self`) and keeps nothing between
requests: an unknown, revoked or expired token gets `401 unauthorized`, a
session token `403 wrong_token_type`, both in the contract's error shape, and
no tool runs. Only a token of type `agent` reaches the transport, together
with its confirmation, which the tool handlers read from the request's ASGI
state (`mcp.request_context.request`).

A request that carries no bearer at all reaches the transport only when every
JSON-RPC message in its body is part of the handshake (`initialize`,
`notifications/initialized`, `ping`), which runs no tool and touches no data;
any other method without a token is `401` before the transport sees it.

The gate runs the transport's own Host and Origin validation first, so a
request refused for DNS-rebinding protection (421, 403) never reaches the
backend, and it applies the transport's request-body limit itself so that an
oversized body is refused in the contract's shape too.
"""

from __future__ import annotations

import json
import logging
from collections import deque
from collections.abc import Callable
from dataclasses import dataclass

from mcp.server.transport_security import TransportSecurityMiddleware, TransportSecuritySettings
from starlette.datastructures import Headers
from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from paper_boxing.common.client import BackendClient, BackendError, BackendUnreachable
from paper_boxing.common.schema import ErrorCode, TokenSelf
from paper_boxing.common.scopes import TokenType

logger = logging.getLogger(__name__)

# The handshake: JSON-RPC methods that run no tool and read no data, so a request
# without a bearer may still send them (an MCP client initializes before it lists tools).
EXEMPT_METHODS: frozenset[str] = frozenset({"initialize", "notifications/initialized", "ping"})

# Where the gate leaves the confirmation in the request's ASGI `state` for the handlers.
STATE_KEY = "paper_boxing_confirmed"

# The client's own code for a backend that does not answer (`BackendUnreachable`), answered as 503.
BACKEND_UNREACHABLE = "backend_unreachable"

REALM = "paper-boxing"
CHALLENGE_MISSING = f'Bearer realm="{REALM}"'
CHALLENGE_INVALID = f'Bearer realm="{REALM}", error="invalid_token"'


@dataclass(frozen=True)
class Confirmed:
    """One request's confirmed agent token: the bearer to forward, and what the backend says it is."""

    bearer: str
    token: TokenSelf


class AuthRefused(Exception):
    """The request must not reach the transport; answered with `status` in the contract's error shape."""

    def __init__(self, status: int, code: str, message: str, *, challenge: str | None = None) -> None:
        super().__init__(f"{status} {code}: {message}")
        self.status = status
        self.code = code
        self.message = message
        self.challenge = challenge


def error_response(status: int, code: str, message: str, *, challenge: str | None = None) -> JSONResponse:
    """`{"error": {"code", "message"}}` with `status`; a 401 carries its `WWW-Authenticate` challenge."""
    headers = {"WWW-Authenticate": challenge} if challenge else None
    return JSONResponse({"error": {"code": code, "message": message}}, status_code=status, headers=headers)


def bearer_from(headers: Headers) -> str | None:
    """The token of an `Authorization: Bearer <token>` header; None when absent, empty or another scheme."""
    scheme, _, value = headers.get("authorization", "").partition(" ")
    if scheme.lower() != "bearer" or not value.strip():
        return None
    return value.strip()


def needs_token(body: bytes) -> bool:
    """Whether a request without a bearer must be refused.

    True when any well-formed JSON-RPC message in the body names a method outside
    the handshake. A body the transport will reject anyway (not JSON, not a
    message) is left to it, and a JSON-RPC response has no method.
    """
    try:
        parsed = json.loads(body)
    except ValueError:
        return False
    messages = parsed if isinstance(parsed, list) else [parsed]
    for message in messages:
        if not isinstance(message, dict):
            continue
        method = message.get("method")
        if isinstance(method, str) and method not in EXEMPT_METHODS:
            return True
    return False


async def confirm(backend: BackendClient, bearer: str) -> Confirmed:
    """Safeguards 1 to 3: ask the backend what the bearer is, and accept a token of type `agent` only.

    Nothing is remembered: every call asks the backend afresh.
    """
    try:
        me = await backend.token_self(bearer)
    except BackendUnreachable as e:
        raise AuthRefused(
            503, BACKEND_UNREACHABLE, f"the backend could not be reached to confirm the token: {e.message}"
        ) from e
    except BackendError as e:
        if e.status == 401:
            raise AuthRefused(
                401,
                ErrorCode.UNAUTHORIZED.value,
                f"the backend does not confirm the token (unknown, revoked or expired): {e.message}",
                challenge=CHALLENGE_INVALID,
            ) from e
        raise AuthRefused(e.status, e.code, f"the backend could not confirm the token: {e.message}") from e
    if me.type is not TokenType.AGENT:
        raise AuthRefused(
            403,
            ErrorCode.WRONG_TOKEN_TYPE.value,
            f"the token is a {me.type.value} token; the MCP server accepts agent tokens only "
            "(create one in the UI under Tokens)",
        )
    return Confirmed(bearer=bearer, token=me)


def confirmed_from(request: Request | None) -> Confirmed | None:
    """The confirmation the gate left on this request's ASGI state, or None when the gate did not run."""
    if request is None:
        return None
    value = (request.scope.get("state") or {}).get(STATE_KEY)
    return value if isinstance(value, Confirmed) else None


class BodyTooLarge(Exception):
    def __init__(self, limit: int) -> None:
        super().__init__(f"request body above {limit} bytes")
        self.limit = limit


async def _buffer(receive: Receive, limit: int) -> tuple[bytes, Receive]:
    """Read the request body once, within `limit`, and return it with a `receive` that replays it."""
    chunks = bytearray()
    trailing: Message | None = None
    received_request = False
    complete = False
    while True:
        message = await receive()
        if message["type"] != "http.request":
            trailing = message
            break
        received_request = True
        chunk = message.get("body", b"")
        if len(chunks) + len(chunk) > limit:
            raise BodyTooLarge(limit)
        chunks.extend(chunk)
        if not message.get("more_body", False):
            complete = True
            break
    body = bytes(chunks)
    cached: deque[Message] = deque()
    if received_request:
        cached.append({"type": "http.request", "body": body, "more_body": not complete})
    if trailing is not None:
        cached.append(trailing)

    async def replay() -> Message:
        if cached:
            return cached.popleft()
        return await receive()

    return body, replay


class TokenGate:
    """The ASGI layer around the transport: transport security, then the token, then the transport."""

    def __init__(
        self,
        app: ASGIApp,
        backend: Callable[[], BackendClient],
        *,
        security: TransportSecuritySettings | None,
        max_body_bytes: int,
    ) -> None:
        self.app = app
        self._backend = backend
        self._security = TransportSecurityMiddleware(security)
        self.max_body_bytes = max_body_bytes

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        request = Request(scope, receive)
        refused = await self._security.validate_request(request, is_post=request.method == "POST")
        if refused is not None:
            await refused(scope, receive, send)
            return

        bearer = bearer_from(request.headers)
        confirmed: Confirmed | None = None
        if bearer is not None:
            try:
                confirmed = await confirm(self._backend(), bearer)
            except AuthRefused as e:
                logger.info("refused /mcp request: %s %s", e.status, e.code)
                await error_response(e.status, e.code, e.message, challenge=e.challenge)(scope, receive, send)
                return

        declared = request.headers.get("content-length")
        if declared is not None and declared.isdigit() and int(declared) > self.max_body_bytes:
            await self._too_large()(scope, receive, send)
            return
        try:
            body, replay = await _buffer(receive, self.max_body_bytes)
        except BodyTooLarge:
            await self._too_large()(scope, receive, send)
            return

        if confirmed is None and needs_token(body):
            await error_response(
                401,
                ErrorCode.UNAUTHORIZED.value,
                "a bearer token is required: send `Authorization: Bearer <agent token>` on every request",
                challenge=CHALLENGE_MISSING,
            )(scope, receive, send)
            return

        if confirmed is not None:
            scope.setdefault("state", {})[STATE_KEY] = confirmed
        await self.app(scope, replay, send)

    def _too_large(self) -> JSONResponse:
        return error_response(
            413,
            ErrorCode.PAYLOAD_TOO_LARGE.value,
            f"the request body is above the {self.max_body_bytes} byte limit of the MCP endpoint; "
            "a larger file goes through the backend's REST API with the same token",
        )
