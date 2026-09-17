# SPDX-FileCopyrightText: 2026 Gary Frattarola <garyf@parkviewlab.ai>
#
# SPDX-License-Identifier: MIT OR Apache-2.0

"""The three safeguards against token passthrough (docs/architecture.md, "The MCP server").

`TokenGate` is the ASGI callable behind `/mcp`, in front of the Streamable-HTTP
transport. Every POST needs a bearer, the handshake included: an MCP client
sends its `Authorization` header on every request. The gate confirms the
bearer with the backend (`GET /api/v1/tokens/self`) on every request and keeps
nothing between requests. An unknown, revoked or expired token gets `401
unauthorized`; a session token `403 wrong_token_type`; a backend that cannot be
reached, or that does not answer the confirmation as the contract says, `503
backend_unreachable`. Each is answered in the contract's error shape, with a
fixed message, and no tool runs. Only a confirmed agent token reaches the
transport, together with its confirmation on the request's ASGI state, from
which the handlers read it (`mcp.request_context.request`).

Before the backend is asked, the gate answers what costs no confirmation, in
the same shape: the transport's own Host, Origin and Content-Type validation
(421, 403, 400) and a declared body above the transport's limit (413). The
body itself is not read here: it passes to the SDK's request-body limit and
the transport unchanged.
"""

from __future__ import annotations

import json
import logging
from collections.abc import Callable
from dataclasses import dataclass

from mcp.server.transport_security import TransportSecurityMiddleware, TransportSecuritySettings
from pydantic import ValidationError
from starlette.datastructures import Headers
from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Receive, Scope, Send

from paper_boxing.common.client import BackendClient, BackendError, BackendUnreachable
from paper_boxing.common.errors import error_response
from paper_boxing.common.schema import ErrorCode, TokenSelf
from paper_boxing.common.scopes import TokenType

logger = logging.getLogger(__name__)

# Where the gate leaves the confirmation in the request's ASGI `state` for the handlers.
STATE_KEY = "paper_boxing_confirmed"

REALM = "paper-boxing"
CHALLENGE_MISSING = {"WWW-Authenticate": f'Bearer realm="{REALM}"'}
CHALLENGE_INVALID = {"WWW-Authenticate": f'Bearer realm="{REALM}", error="invalid_token"'}

# The gate's messages are fixed: nothing the backend answered is ever spliced into a response.
MSG_MISSING = "a bearer token is required: send `Authorization: Bearer <agent token>` on every request"
MSG_NOT_CONFIRMED = "the backend does not confirm the token: it is unknown, revoked or expired"
MSG_WRONG_TYPE = "the token is a session token; the MCP server accepts agent tokens only (create one in the UI under Tokens)"
MSG_UNCONFIRMED = "the backend could not be reached to confirm the token, or did not answer as expected"


@dataclass(frozen=True)
class Confirmed:
    """One request's confirmed agent token: the bearer to forward, and what the backend says it is."""

    bearer: str
    token: TokenSelf


class AuthRefused(Exception):
    """The request must not reach the transport; answered with `status` in the contract's error shape."""

    def __init__(
        self, status: int, code: ErrorCode, message: str, *, headers: dict[str, str] | None = None
    ) -> None:
        super().__init__(f"{status} {code.value}: {message}")
        self.status = status
        self.code = code
        self.message = message
        self.headers = headers

    def response(self) -> JSONResponse:
        return error_response(self.status, self.code, self.message, headers=self.headers)


def bearer_from(headers: Headers) -> str | None:
    """The token of an `Authorization: Bearer <token>` header; None when absent, empty or another scheme."""
    scheme, _, value = headers.get("authorization", "").partition(" ")
    if scheme.lower() != "bearer" or not value.strip():
        return None
    return value.strip()


async def confirm(backend: BackendClient, bearer: str) -> Confirmed:
    """Safeguards 1 to 3: ask the backend what the bearer is, and accept a token of type `agent` only.

    Nothing is remembered between calls. Raises `AuthRefused` and nothing else: a backend 401 is
    401, a session token 403, and everything else the backend does (unreachable, another status,
    an answer that is not a `TokenSelf`) is 503 with the detail logged here, never answered.
    """
    try:
        me = await backend.token_self(bearer)
    except BackendUnreachable as e:
        logger.warning("token confirmation: the backend is unreachable: %.200s", e.message)
        raise AuthRefused(503, ErrorCode.BACKEND_UNREACHABLE, MSG_UNCONFIRMED) from e
    except BackendError as e:
        if e.status == 401:
            raise AuthRefused(
                401, ErrorCode.UNAUTHORIZED, MSG_NOT_CONFIRMED, headers=CHALLENGE_INVALID
            ) from e
        logger.warning("token confirmation: the backend answered %s %s: %.200s", e.status, e.code, e.message)
        raise AuthRefused(503, ErrorCode.BACKEND_UNREACHABLE, MSG_UNCONFIRMED) from e
    except (json.JSONDecodeError, ValidationError) as e:
        logger.warning("token confirmation: the backend's 200 is not a TokenSelf: %.200s", e)
        raise AuthRefused(503, ErrorCode.BACKEND_UNREACHABLE, MSG_UNCONFIRMED) from e
    except Exception as e:
        logger.exception("token confirmation failed")
        raise AuthRefused(503, ErrorCode.BACKEND_UNREACHABLE, MSG_UNCONFIRMED) from e
    if me.type is not TokenType.AGENT:
        raise AuthRefused(403, ErrorCode.WRONG_TOKEN_TYPE, MSG_WRONG_TYPE)
    return Confirmed(bearer=bearer, token=me)


def confirmed_from(request: Request | None) -> Confirmed | None:
    """The confirmation the gate left on this request's ASGI state, or None when the gate did not run."""
    if request is None:
        return None
    value = (request.scope.get("state") or {}).get(STATE_KEY)
    return value if isinstance(value, Confirmed) else None


class TokenGate:
    """The ASGI callable at /mcp: transport security, then the declared size, then the token, then the transport."""

    def __init__(
        self,
        app: ASGIApp,
        backend: Callable[[], BackendClient],
        *,
        security: TransportSecuritySettings,
        max_body_bytes: int,
    ) -> None:
        self.app = app
        self._backend = backend
        # Required, not optional: TransportSecurityMiddleware(None) turns rebinding protection off silently.
        self._security = TransportSecurityMiddleware(security)
        self.max_body_bytes = max_body_bytes

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        request = Request(scope, receive)
        try:
            await self._check_transport(request)
            self._check_declared_length(request)
            bearer = bearer_from(request.headers)
            if bearer is None:
                raise AuthRefused(401, ErrorCode.UNAUTHORIZED, MSG_MISSING, headers=CHALLENGE_MISSING)
            confirmed = await confirm(self._backend(), bearer)
        except AuthRefused as e:
            logger.info("refused /mcp request: %s %s", e.status, e.code.value)
            await e.response()(scope, receive, send)
            return
        except Exception:  # the gate answers; it never raises into the server
            logger.exception("the gate failed before the token was confirmed")
            await error_response(503, ErrorCode.BACKEND_UNREACHABLE, MSG_UNCONFIRMED)(scope, receive, send)
            return
        scope.setdefault("state", {})[STATE_KEY] = confirmed
        await self.app(scope, receive, send)

    async def _check_transport(self, request: Request) -> None:
        """The transport's own checks first, answered in the contract's shape, so a refused request costs no confirmation."""
        if request.method != "POST":
            raise AuthRefused(
                405, ErrorCode.METHOD_NOT_ALLOWED, "/mcp accepts POST only", headers={"Allow": "POST"}
            )
        refused = await self._security.validate_request(request, is_post=True)
        if refused is None:
            return
        if refused.status_code == 421:
            host = request.headers.get("host")
            raise AuthRefused(
                421,
                ErrorCode.FORBIDDEN,
                f"the Host {host!r} is not in PAPER_BOXING_MCP_ALLOWED_HOSTS; add the host and port "
                "agents connect with (DNS-rebinding protection)",
            )
        if refused.status_code == 403:
            origin = request.headers.get("origin")
            raise AuthRefused(
                403,
                ErrorCode.FORBIDDEN,
                f"the Origin {origin!r} is not in PAPER_BOXING_MCP_ALLOWED_ORIGINS (DNS-rebinding protection)",
            )
        raise AuthRefused(
            refused.status_code,
            ErrorCode.BAD_REQUEST,
            "the Content-Type of a POST to /mcp must be application/json",
        )

    def _check_declared_length(self, request: Request) -> None:
        declared = request.headers.get("content-length")
        if declared is not None and declared.isdigit() and int(declared) > self.max_body_bytes:
            raise AuthRefused(
                413,
                ErrorCode.PAYLOAD_TOO_LARGE,
                f"the request body is above the {self.max_body_bytes} byte limit of the MCP endpoint; "
                "a larger file goes through the backend's REST API with the same token",
            )
