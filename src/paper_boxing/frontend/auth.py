# SPDX-FileCopyrightText: 2026 Gary Frattarola <garyf@parkviewlab.ai>
#
# SPDX-License-Identifier: MIT OR Apache-2.0

"""Who is signed in: the session token in `app.storage.user`, and the
middleware that sends an unauthenticated request to `/login`.

`app.storage.user` is NiceGUI's per-browser store, keyed by a cookie that
`PAPER_BOXING_STORAGE_SECRET` signs, so two people (or two browsers) never
share a token. The middleware checks only that a token is stored; whether it
is still valid is the backend's verdict on the next call, and a 401 there
signs the person out (see `layout.report_error`).
"""

from __future__ import annotations

from urllib.parse import quote, urlsplit

from nicegui import app
from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.requests import Request
from starlette.responses import RedirectResponse, Response

from paper_boxing.common.errors import error_response
from paper_boxing.common.schema import ErrorCode
from paper_boxing.frontend.paths import LOGIN_PATH, is_fetch, is_public

__all__ = ["LOGIN_PATH", "AuthMiddleware", "is_public", "login_url", "safe_next"]

_TOKEN = "token"
_USERNAME = "username"
_USER_ID = "user_id"


def login_url(next_path: str | None) -> str:
    """The sign-in page, remembering where to return afterwards."""
    if not next_path or next_path == "/":
        return LOGIN_PATH
    return f"{LOGIN_PATH}?next={quote(next_path, safe='')}"


def safe_next(candidate: str | None) -> str:
    """A return path from the query string, or "/" when it is missing or points off-site.

    Off-site: a scheme or a host, a path not starting with "/", and a second
    character that is a slash or a backslash, since a browser reads both
    `//evil.test/` and `/\\evil.test/` as a host, whatever `urlsplit` makes of them.
    """
    if not candidate or not candidate.startswith("/"):
        return "/"
    if len(candidate) > 1 and candidate[1] in "/\\":
        return "/"
    parts = urlsplit(candidate)
    if parts.scheme or parts.netloc:
        return "/"
    return candidate


# ---- the signed-in person, from app.storage.user ----


def token() -> str | None:
    value = app.storage.user.get(_TOKEN)
    return value if isinstance(value, str) and value else None


def session_token() -> str:
    """The session token behind a page or a handler; the middleware admits none without one."""
    value = token()
    if value is None:
        raise RuntimeError(
            "no session token is stored for this browser; the middleware should have redirected"
        )
    return value


def username() -> str:
    value = app.storage.user.get(_USERNAME)
    return value if isinstance(value, str) else ""


def user_id() -> str:
    value = app.storage.user.get(_USER_ID)
    return value if isinstance(value, str) else ""


def sign_in(session: str, user_name: str, uid: str) -> None:
    app.storage.user.update({_TOKEN: session, _USERNAME: user_name, _USER_ID: uid})


def sign_out() -> None:
    """Forget the session in this browser; the backend's session is ended separately, when it can be."""
    for key in (_TOKEN, _USERNAME, _USER_ID):
        app.storage.user.pop(key, None)


# ---- the middleware ----


class AuthMiddleware(BaseHTTPMiddleware):
    """Send a request for a page to `/login` unless a session token is stored for this browser;
    answer a fetch (a download) with a 401 in the contract's body instead.

    NiceGUI's own middleware runs outside this one and has already created the
    per-browser storage, so `app.storage.user` is readable here.
    """

    async def dispatch(self, request: Request, call_next: RequestResponseEndpoint) -> Response:
        path = request.url.path
        if is_public(path) or token() is not None:
            return await call_next(request)
        if is_fetch(path):
            return error_response(401, ErrorCode.UNAUTHORIZED, "sign in to download files")
        wanted = f"{path}?{request.url.query}" if request.url.query else path
        return RedirectResponse(login_url(wanted), status_code=303)


def install_middleware() -> None:
    """Add the middleware once, innermost, so the session cookie has been read before it runs."""
    if any(m.cls is AuthMiddleware for m in app.user_middleware):
        return
    app.middleware_stack = None  # rebuilt on the next request; a test re-installs after NiceGUI's reset
    app.add_middleware(AuthMiddleware)
