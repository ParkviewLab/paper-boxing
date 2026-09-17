# SPDX-FileCopyrightText: 2026 Gary Frattarola <garyf@parkviewlab.ai>
#
# SPDX-License-Identifier: MIT OR Apache-2.0

"""The REST route table (docs/api.md): every route of the backend's API with
its method, path template and access rule.

The backend registers exactly these routes, the fake backend registers exactly
these routes (a test checks it), and the client builds its URLs from them, so
the three cannot drift apart. Order matters where FastAPI would otherwise match
a literal segment as a parameter: `/tokens/self` precedes `/tokens/{token_id}`
and `/users/self/password` precedes `/users/{user_id}`.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from enum import StrEnum
from urllib.parse import quote

from paper_boxing.common.scopes import Scope, TokenType, scope_allows

API_PREFIX = "/api/v1"

# The header a request carries when it came through the MCP server; the backend logs it.
VIA_HEADER = "X-Paper-Boxing-Via"
VIA_MCP = "mcp"


class Access(StrEnum):
    """Who may call a route."""

    PUBLIC = "public"  # no token
    ANY_TOKEN = "any_token"  # any valid session or agent token, whatever its scope
    READ_ONLY = "read_only"  # a token of at least this scope
    READ_WRITE = "read_write"
    REMOVE_DESTRUCTIVE = "remove_destructive"
    SESSION = "session"  # a session token only: account and token management is a person's act


def access_allows(access: Access, token_type: TokenType | None, scope: Scope | None) -> bool:
    """Decide a call under the access rule: `token_type` and `scope` are None when no valid token was presented."""
    if access is Access.PUBLIC:
        return True
    if token_type is None or scope is None:
        return False
    if access is Access.ANY_TOKEN:
        return True
    if access is Access.SESSION:
        return token_type is TokenType.SESSION
    return scope_allows(scope, Scope(access.value))


@dataclass(frozen=True)
class RouteSpec:
    name: str
    method: str
    path: str  # FastAPI template; `{path:path}` matches the rest of the URL
    access: Access
    summary: str


ROUTES: tuple[RouteSpec, ...] = (
    # authentication
    RouteSpec("login", "POST", f"{API_PREFIX}/auth/login", Access.PUBLIC, "sign in; returns a session token"),
    RouteSpec("logout", "POST", f"{API_PREFIX}/auth/logout", Access.SESSION, "end the calling session"),
    # sites
    RouteSpec("list_sites", "GET", f"{API_PREFIX}/sites", Access.READ_ONLY, "every site with its counts"),
    RouteSpec(
        "create_site", "POST", f"{API_PREFIX}/sites", Access.READ_WRITE, "create a site from a display name"
    ),
    RouteSpec("get_site", "GET", f"{API_PREFIX}/sites/{{slug}}", Access.READ_ONLY, "one site"),
    RouteSpec(
        "delete_site",
        "DELETE",
        f"{API_PREFIX}/sites/{{slug}}",
        Access.REMOVE_DESTRUCTIVE,
        "delete a site and everything in it; needs ?confirm=<slug>",
    ),
    # files and folders
    RouteSpec(
        "list_files",
        "GET",
        f"{API_PREFIX}/sites/{{slug}}/files",
        Access.READ_ONLY,
        "list one folder (?path=)",
    ),
    RouteSpec(
        "upload_file",
        "PUT",
        f"{API_PREFIX}/sites/{{slug}}/files/{{path:path}}",
        Access.READ_WRITE,
        "upload one file as the raw body (?overwrite=)",
    ),
    RouteSpec(
        "download_file",
        "GET",
        f"{API_PREFIX}/sites/{{slug}}/files/{{path:path}}",
        Access.READ_ONLY,
        "download one file as an attachment",
    ),
    RouteSpec(
        "delete_file",
        "DELETE",
        f"{API_PREFIX}/sites/{{slug}}/files/{{path:path}}",
        Access.REMOVE_DESTRUCTIVE,
        "delete one file",
    ),
    RouteSpec(
        "upload_batch",
        "POST",
        f"{API_PREFIX}/sites/{{slug}}/uploads",
        Access.READ_WRITE,
        "upload many files as multipart; each part's filename is its relative path (?overwrite=)",
    ),
    RouteSpec(
        "download_archive",
        "GET",
        f"{API_PREFIX}/sites/{{slug}}/archive",
        Access.READ_ONLY,
        "download a folder (?path=) or the whole site as a zip",
    ),
    RouteSpec(
        "delete_folder",
        "DELETE",
        f"{API_PREFIX}/sites/{{slug}}/folders/{{path:path}}",
        Access.REMOVE_DESTRUCTIVE,
        "delete a folder (?recursive= for a non-empty one)",
    ),
    # access tokens
    RouteSpec(
        "list_tokens", "GET", f"{API_PREFIX}/tokens", Access.SESSION, "every agent token, with its owner"
    ),
    RouteSpec(
        "create_token",
        "POST",
        f"{API_PREFIX}/tokens",
        Access.SESSION,
        "create an agent token; the secret is shown once",
    ),
    RouteSpec(
        "token_self",
        "GET",
        f"{API_PREFIX}/tokens/self",
        Access.ANY_TOKEN,
        "the calling token's id, name, type and scope",
    ),
    RouteSpec(
        "revoke_token", "DELETE", f"{API_PREFIX}/tokens/{{token_id}}", Access.SESSION, "revoke an agent token"
    ),
    # users
    RouteSpec("list_users", "GET", f"{API_PREFIX}/users", Access.SESSION, "every account"),
    RouteSpec("create_user", "POST", f"{API_PREFIX}/users", Access.SESSION, "add an account"),
    RouteSpec(
        "change_password",
        "POST",
        f"{API_PREFIX}/users/self/password",
        Access.SESSION,
        "change the calling user's password; signs out that user's other sessions",
    ),
    RouteSpec(
        "delete_user",
        "DELETE",
        f"{API_PREFIX}/users/{{user_id}}",
        Access.SESSION,
        "remove an account (409 for the last one)",
    ),
)

ROUTES_BY_NAME: dict[str, RouteSpec] = {route.name: route for route in ROUTES}

_PARAM_RE = re.compile(r"\{(\w+)(?::path)?\}")


def route(name: str) -> RouteSpec:
    return ROUTES_BY_NAME[name]


def build_path(name: str, **params: str) -> str:
    """Render a route's path template with URL-quoted parameters (a `path` keeps its slashes)."""
    spec = ROUTES_BY_NAME[name]

    def substitute(match: re.Match[str]) -> str:
        key = match.group(1)
        value = params[key]
        return quote(value, safe="/") if key == "path" else quote(value, safe="")

    return _PARAM_RE.sub(substitute, spec.path)


def site_url(public_sites_url: str, slug: str) -> str:
    """The served address of a site, `<public_sites_url>/<slug>/`."""
    return f"{public_sites_url.rstrip('/')}/{slug}/"
