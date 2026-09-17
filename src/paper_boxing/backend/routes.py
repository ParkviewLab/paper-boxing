# SPDX-FileCopyrightText: 2026 Gary Frattarola <garyf@parkviewlab.ai>
#
# SPDX-License-Identifier: MIT OR Apache-2.0

"""The REST routes of docs/api.md, registered from the route table in
`paper_boxing.common.routes` so the real app and the fake cannot drift.

Every non-public route takes its caller from `requires(<route name>)`: a
missing, unknown, revoked or expired token is `401 unauthorized` before any
other check; an agent token on a session-only route is `403
session_required`; a scope below the route's is `403 forbidden`. Each
authenticated call is logged under the user and the token that made it, with
the `X-Paper-Boxing-Via` header when the MCP server relayed it; every change
is logged with what it did. No password or token secret is ever logged.
"""

from __future__ import annotations

import logging
import mimetypes
from collections.abc import Awaitable, Callable
from typing import Annotated
from urllib.parse import quote

from fastapi import Depends, FastAPI, Request, Response
from starlette.responses import FileResponse, JSONResponse

from paper_boxing.backend.accounts import Actor, public_token, public_user
from paper_boxing.backend.context import Backend
from paper_boxing.backend.db import Conflict, SiteRow
from paper_boxing.common.errors import ApiError
from paper_boxing.common.naming import path_name, slug_from_name, validate_site_path, validate_slug
from paper_boxing.common.routes import VIA_HEADER, Access, access_allows, route, site_url
from paper_boxing.common.schema import (
    ChangePasswordRequest,
    CreateSiteRequest,
    CreateTokenRequest,
    CreateUserRequest,
    ErrorCode,
    FileListing,
    LoginRequest,
    LoginResponse,
    Site,
    SiteList,
    TokenCreated,
    TokenList,
    TokenSelf,
    UploadResult,
    User,
    UserList,
)

logger = logging.getLogger(__name__)

Dependency = Callable[[Request], Awaitable[Actor]]


# ---------------------------------------------------------------------------
# Authorization and logging


def _backend(request: Request) -> Backend:
    return request.app.state.backend


def _who(request: Request) -> str:
    actor: Actor | None = getattr(request.state, "actor", None)
    if actor is None:
        return "anonymous"
    via = request.headers.get(VIA_HEADER)
    text = f"user={actor.user.username} token={actor.token.id} type={actor.token.type.value}"
    return f"{text} via={via}" if via else text


def audit(request: Request, event: str, **fields: object) -> None:
    """One log line per change: what happened, who did it, and the details that name the target."""
    details = " ".join(f"{key}={value}" for key, value in fields.items())
    logger.info("%s %s %s", event, _who(request), details)


def requires(name: str) -> Dependency:
    """The dependency that enforces a route's access rule and records who called."""
    spec = route(name)
    if spec.access is Access.PUBLIC:
        raise ValueError(f"route {name!r} is public; it takes no authorization dependency")

    async def dependency(request: Request) -> Actor:
        backend = _backend(request)
        actor = backend.accounts.resolve_bearer(request.headers.get("authorization"))
        if actor is None:
            raise ApiError(401, ErrorCode.UNAUTHORIZED, "a valid bearer token is required")
        request.state.actor = actor
        logger.info("route=%s %s scope=%s", spec.name, _who(request), actor.token.scope.value)
        if not access_allows(spec.access, actor.token.type, actor.token.scope):
            if spec.access is Access.SESSION:
                logger.warning("refused route=%s %s: session_required", spec.name, _who(request))
                raise ApiError(
                    403,
                    ErrorCode.SESSION_REQUIRED,
                    "this route needs a signed-in user's session, not an agent token",
                )
            logger.warning("refused route=%s %s: forbidden", spec.name, _who(request))
            raise ApiError(
                403,
                ErrorCode.FORBIDDEN,
                f"this route needs the {spec.access.value} scope; the token has {actor.token.scope.value}",
            )
        return actor

    return dependency


# ---------------------------------------------------------------------------
# Helpers


def _site(backend: Backend, slug: str) -> SiteRow:
    try:
        validate_slug(slug)
    except ValueError as e:
        raise ApiError(404, ErrorCode.NOT_FOUND, f"no site {slug!r}") from e
    site = backend.db.site_by_slug(slug)
    if site is None:
        raise ApiError(404, ErrorCode.NOT_FOUND, f"no site {slug!r}")
    return site


def _public_site(backend: Backend, site: SiteRow) -> Site:
    file_count, size = backend.storage.site_totals(site.slug)
    return Site(
        slug=site.slug,
        name=site.name,
        url=site_url(backend.config.public_sites_url, site.slug),
        created_at=site.created_at,
        file_count=file_count,
        bytes=size,
    )


def _valid_path(raw: str, *, allow_root: bool = False) -> str:
    try:
        return validate_site_path(raw, allow_root=allow_root)
    except ValueError as e:
        raise ApiError(400, ErrorCode.INVALID_PATH, f"invalid path {raw!r}: {e}") from e


def _declared_length(request: Request) -> int | None:
    raw = request.headers.get("content-length")
    if raw is None:
        return None
    try:
        return int(raw)
    except ValueError:
        return None


def _attachment(name: str) -> str:
    ascii_name = name.encode("ascii", "replace").decode("ascii").replace('"', "'")
    return f"attachment; filename=\"{ascii_name}\"; filename*=UTF-8''{quote(name)}"


# ---------------------------------------------------------------------------
# The routes, in the table's order


def register_routes(app: FastAPI) -> None:
    # ---- authentication ----

    @app.post(route("login").path, response_model=LoginResponse, status_code=200, tags=["auth"])
    async def login(body: LoginRequest, request: Request) -> LoginResponse:
        backend = _backend(request)
        result = backend.accounts.login(body.username, body.password)
        if result is None:
            logger.warning("login failed username=%s", body.username)
            raise ApiError(401, ErrorCode.INVALID_CREDENTIALS, "unknown username or wrong password")
        user, session, secret = result
        logger.info("login user=%s token=%s", user.username, session.id)
        assert session.expires_at is not None
        return LoginResponse(token=secret, expires_at=session.expires_at, user=public_user(user))

    @app.post(route("logout").path, status_code=204, tags=["auth"])
    async def logout(request: Request, actor: Annotated[Actor, Depends(requires("logout"))]) -> Response:
        _backend(request).accounts.logout(actor.token)
        audit(request, "logout")
        return Response(status_code=204)

    # ---- sites ----

    @app.get(route("list_sites").path, response_model=SiteList, tags=["sites"])
    async def list_sites(request: Request, _: Annotated[Actor, Depends(requires("list_sites"))]) -> SiteList:
        backend = _backend(request)
        return SiteList(sites=[_public_site(backend, s) for s in backend.db.list_sites()])

    @app.post(route("create_site").path, response_model=Site, status_code=201, tags=["sites"])
    async def create_site(
        body: CreateSiteRequest, request: Request, _: Annotated[Actor, Depends(requires("create_site"))]
    ) -> Site:
        backend = _backend(request)
        try:
            slug = slug_from_name(body.name)
        except ValueError as e:
            raise ApiError(400, ErrorCode.INVALID_NAME, str(e)) from e
        site = await backend.storage.create_site(slug, body.name.strip(), backend.clock.now())
        audit(request, "create_site", site=slug)
        return _public_site(backend, site)

    @app.get(route("get_site").path, response_model=Site, tags=["sites"])
    async def get_site(
        slug: str, request: Request, _: Annotated[Actor, Depends(requires("get_site"))]
    ) -> Site:
        backend = _backend(request)
        return _public_site(backend, _site(backend, slug))

    @app.delete(route("delete_site").path, status_code=204, tags=["sites"])
    async def delete_site(
        slug: str,
        request: Request,
        _: Annotated[Actor, Depends(requires("delete_site"))],
        confirm: str | None = None,
    ) -> Response:
        backend = _backend(request)
        site = _site(backend, slug)
        if confirm != site.slug:
            raise ApiError(400, ErrorCode.CONFIRM_MISMATCH, f"pass ?confirm={site.slug} to delete the site")
        await backend.storage.delete_site(site.slug)
        audit(request, "delete_site", site=site.slug)
        return Response(status_code=204)

    # ---- files and folders ----

    @app.get(route("list_files").path, response_model=FileListing, tags=["files"])
    async def list_files(
        slug: str,
        request: Request,
        _: Annotated[Actor, Depends(requires("list_files"))],
        path: str = "",
    ) -> FileListing:
        backend = _backend(request)
        site = _site(backend, slug)
        folder = _valid_path(path, allow_root=True)
        return backend.storage.list_folder(site.slug, folder)

    @app.put(
        route("upload_file").path,
        response_model=UploadResult,
        status_code=201,
        tags=["files"],
        openapi_extra={"requestBody": {"content": {"application/octet-stream": {}}, "required": True}},
    )
    async def upload_file(
        slug: str,
        path: str,
        request: Request,
        _: Annotated[Actor, Depends(requires("upload_file"))],
        overwrite: bool = False,
    ) -> JSONResponse:
        backend = _backend(request)
        site = _site(backend, slug)
        target = _valid_path(path)
        result = await backend.storage.write_file(
            site.slug,
            target,
            request.stream(),
            overwrite=overwrite,
            declared_length=_declared_length(request),
        )
        audit(
            request, "upload_file", site=site.slug, path=target, bytes=result.bytes, replaced=result.replaced
        )
        return JSONResponse(result.model_dump(mode="json"), status_code=200 if result.replaced else 201)

    @app.get(route("download_file").path, tags=["files"], response_class=FileResponse)
    async def download_file(
        slug: str, path: str, request: Request, _: Annotated[Actor, Depends(requires("download_file"))]
    ) -> FileResponse:
        backend = _backend(request)
        site = _site(backend, slug)
        target = _valid_path(path)
        file = backend.storage.file_path(site.slug, target)
        content_type = mimetypes.guess_type(target)[0] or "application/octet-stream"
        return FileResponse(
            file, media_type=content_type, headers={"Content-Disposition": _attachment(path_name(target))}
        )

    @app.delete(route("delete_file").path, status_code=204, tags=["files"])
    async def delete_file(
        slug: str, path: str, request: Request, _: Annotated[Actor, Depends(requires("delete_file"))]
    ) -> Response:
        backend = _backend(request)
        site = _site(backend, slug)
        target = _valid_path(path)
        await backend.storage.delete_file(site.slug, target)
        audit(request, "delete_file", site=site.slug, path=target)
        return Response(status_code=204)

    @app.delete(route("delete_folder").path, status_code=204, tags=["files"])
    async def delete_folder(
        slug: str,
        path: str,
        request: Request,
        _: Annotated[Actor, Depends(requires("delete_folder"))],
        recursive: bool = False,
    ) -> Response:
        backend = _backend(request)
        site = _site(backend, slug)
        target = _valid_path(path)
        await backend.storage.delete_folder(site.slug, target, recursive=recursive)
        audit(request, "delete_folder", site=site.slug, path=target, recursive=recursive)
        return Response(status_code=204)

    # ---- access tokens ----

    @app.get(route("list_tokens").path, response_model=TokenList, tags=["tokens"])
    async def list_tokens(
        request: Request, _: Annotated[Actor, Depends(requires("list_tokens"))]
    ) -> TokenList:
        return TokenList(tokens=_backend(request).accounts.list_agent_tokens())

    @app.post(route("create_token").path, response_model=TokenCreated, status_code=201, tags=["tokens"])
    async def create_token(
        body: CreateTokenRequest, request: Request, actor: Annotated[Actor, Depends(requires("create_token"))]
    ) -> TokenCreated:
        token, secret = _backend(request).accounts.create_agent_token(
            actor.user, body.name.strip(), body.scope
        )
        audit(request, "create_token", created=token.id, scope=token.scope.value)
        return TokenCreated(token=public_token(token, actor.user.username), secret=secret)

    @app.get(route("token_self").path, response_model=TokenSelf, tags=["tokens"])
    async def token_self(
        request: Request, actor: Annotated[Actor, Depends(requires("token_self"))]
    ) -> TokenSelf:
        return _backend(request).accounts.token_self(actor)

    @app.delete(route("revoke_token").path, status_code=204, tags=["tokens"])
    async def revoke_token(
        token_id: str, request: Request, _: Annotated[Actor, Depends(requires("revoke_token"))]
    ) -> Response:
        if not _backend(request).accounts.revoke_agent_token(token_id):
            raise ApiError(404, ErrorCode.NOT_FOUND, f"no agent token {token_id!r}")
        audit(request, "revoke_token", revoked=token_id)
        return Response(status_code=204)

    # ---- users ----

    @app.get(route("list_users").path, response_model=UserList, tags=["users"])
    async def list_users(request: Request, _: Annotated[Actor, Depends(requires("list_users"))]) -> UserList:
        return UserList(users=[public_user(u) for u in _backend(request).db.list_users()])

    @app.post(route("create_user").path, response_model=User, status_code=201, tags=["users"])
    async def create_user(
        body: CreateUserRequest, request: Request, _: Annotated[Actor, Depends(requires("create_user"))]
    ) -> User:
        try:
            user = _backend(request).accounts.create_user(body.username, body.password)
        except Conflict as e:
            raise ApiError(409, ErrorCode.USER_EXISTS, f"the username {body.username!r} is taken") from e
        audit(request, "create_user", created=user.id, username=user.username)
        return public_user(user)

    @app.post(route("change_password").path, status_code=204, tags=["users"])
    async def change_password(
        body: ChangePasswordRequest,
        request: Request,
        actor: Annotated[Actor, Depends(requires("change_password"))],
    ) -> Response:
        backend = _backend(request)
        if not backend.accounts.verify_password(actor.user, body.current):
            raise ApiError(403, ErrorCode.WRONG_PASSWORD, "the current password is wrong")
        backend.accounts.change_password(actor.user, body.new)
        signed_out = backend.accounts.revoke_other_sessions(actor.user, keep=actor.token)
        audit(request, "change_password", other_sessions_signed_out=signed_out)
        return Response(status_code=204)

    @app.delete(route("delete_user").path, status_code=204, tags=["users"])
    async def delete_user(
        user_id: str, request: Request, _: Annotated[Actor, Depends(requires("delete_user"))]
    ) -> Response:
        backend = _backend(request)
        user = backend.db.user_by_id(user_id)
        if user is None:
            raise ApiError(404, ErrorCode.NOT_FOUND, f"no user {user_id!r}")
        if backend.db.count_users() <= 1:
            raise ApiError(409, ErrorCode.LAST_USER, "the last account cannot be deleted")
        backend.accounts.delete_user(user_id)
        audit(request, "delete_user", deleted=user_id, username=user.username)
        return Response(status_code=204)
