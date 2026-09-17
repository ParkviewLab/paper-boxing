# SPDX-FileCopyrightText: 2026 Gary Frattarola <garyf@parkviewlab.ai>
#
# SPDX-License-Identifier: MIT OR Apache-2.0

"""An in-memory backend that implements the REST contract, for tests.

The frontend's and the MCP server's test suites run against this app instead
of the real backend (docs/design.md section 7), through
`httpx.ASGITransport(app=create_fake_backend())` or FastAPI's `TestClient`.
tests/contract/ exercises it route by route; the backend worker points the
same suite at the real app, which is how the two are kept identical.

It is a faithful model, not a stub: every access rule, every status code and
every error code of docs/api.md, sliding session expiry (with a controllable
clock), folders that exist independently of files, the per-file size
cap, and a record of every request with the token that made it and the
`X-Paper-Boxing-Via` header, so a test can prove that a token was confirmed
on every call. Passwords are stored in plain text here, and nowhere else.
"""

from __future__ import annotations

import mimetypes
import secrets
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from hashlib import sha256
from typing import Annotated
from urllib.parse import quote

from fastapi import Depends, FastAPI, Request, Response
from starlette.responses import JSONResponse

from paper_boxing.common.config import DEFAULT_PUBLIC_SITES_URL
from paper_boxing.common.errors import ApiError, install_error_handlers
from paper_boxing.common.naming import path_name, path_parent, slug_from_name, validate_site_path
from paper_boxing.common.routes import VIA_HEADER, Access, RouteSpec, access_allows, route, site_url
from paper_boxing.common.schema import (
    ChangePasswordRequest,
    CreateSiteRequest,
    CreateTokenRequest,
    CreateUserRequest,
    EntryType,
    ErrorCode,
    FileEntry,
    FileListing,
    Health,
    LoginRequest,
    LoginResponse,
    Site,
    SiteList,
    Token,
    TokenCreated,
    TokenList,
    TokenSelf,
    UploadResult,
    User,
    UserList,
)
from paper_boxing.common.scopes import SESSION_SCOPE, Scope, TokenType

FAKE_VERSION = "0.0.0+fake"


# ---------------------------------------------------------------------------
# State


class Clock:
    """The fake's notion of now; `advance()` moves it so expiry can be tested."""

    def __init__(self) -> None:
        self._offset = timedelta(0)

    def now(self) -> datetime:
        return datetime.now(UTC) + self._offset

    def advance(self, seconds: float) -> None:
        self._offset += timedelta(seconds=seconds)


@dataclass
class FakeUser:
    id: str
    username: str
    password: str
    created_at: datetime

    def public(self) -> User:
        return User(id=self.id, username=self.username, created_at=self.created_at)


@dataclass
class FakeToken:
    id: str
    name: str
    type: TokenType
    scope: Scope
    user_id: str
    created_at: datetime
    last_used_at: datetime | None = None
    expires_at: datetime | None = None
    revoked: bool = False


@dataclass
class FakeFile:
    content: bytes
    modified_at: datetime

    @property
    def sha256(self) -> str:
        return sha256(self.content).hexdigest()


@dataclass
class FakeSite:
    slug: str
    name: str
    created_at: datetime
    files: dict[str, FakeFile] = field(default_factory=dict)
    # Folders exist on their own, as on a filesystem: created with the first file below them,
    # left standing when their last file goes, removed only by delete_folder.
    folders: dict[str, datetime] = field(default_factory=dict)

    def has_folder(self, path: str) -> bool:
        return path == "" or path in self.folders

    def add_folders_for(self, path: str, now: datetime) -> None:
        parent = path_parent(path)
        while parent and parent not in self.folders:
            self.folders[parent] = now
            parent = path_parent(parent)

    def children(self, folder: str) -> tuple[list[str], list[str]]:
        """Direct subfolders and files of `folder` ("" for the root), as full paths."""
        prefix = f"{folder}/" if folder else ""
        subfolders = sorted(p for p in self.folders if path_parent(p) == folder)
        files = sorted(p for p in self.files if path_parent(p) == folder)
        return [p for p in subfolders if p.startswith(prefix)], [p for p in files if p.startswith(prefix)]

    def descendants(self, folder: str) -> tuple[list[str], list[str]]:
        prefix = f"{folder}/" if folder else ""
        return (
            [p for p in self.folders if p.startswith(prefix)],
            [p for p in self.files if p.startswith(prefix)],
        )

    def folder_bytes(self, folder: str) -> int:
        _, files = self.descendants(folder)
        return sum(len(self.files[p].content) for p in files)

    def folder_modified_at(self, folder: str) -> datetime:
        _, files = self.descendants(folder)
        times = [self.files[p].modified_at for p in files] + [self.folders[folder]]
        return max(times)


@dataclass(frozen=True)
class RecordedRequest:
    route: str
    token_id: str | None
    via: str | None


@dataclass
class FakeState:
    """Everything behind the fake app; reachable as `app.state.fake`."""

    public_sites_url: str
    max_upload_bytes: int
    session_days: int
    clock: Clock = field(default_factory=Clock)
    users: dict[str, FakeUser] = field(default_factory=dict)
    tokens: dict[str, FakeToken] = field(default_factory=dict)
    secrets: dict[str, str] = field(default_factory=dict)  # bearer secret -> token id
    sites: dict[str, FakeSite] = field(default_factory=dict)
    requests: list[RecordedRequest] = field(default_factory=list)

    # ---- helpers for tests ----

    def add_user(self, username: str, password: str) -> FakeUser:
        if any(u.username == username for u in self.users.values()):
            raise ValueError(f"user {username!r} exists")
        user = FakeUser(
            id=f"usr_{secrets.token_hex(6)}",
            username=username,
            password=password,
            created_at=self.clock.now(),
        )
        self.users[user.id] = user
        return user

    def user_by_name(self, username: str) -> FakeUser:
        for user in self.users.values():
            if user.username == username:
                return user
        raise KeyError(username)

    def issue_session(self, username: str) -> str:
        """A session token for `username`; returns the bearer secret."""
        user = self.user_by_name(username)
        now = self.clock.now()
        token = FakeToken(
            id=f"tok_{secrets.token_hex(6)}",
            name="session",
            type=TokenType.SESSION,
            scope=SESSION_SCOPE,
            user_id=user.id,
            created_at=now,
            expires_at=now + timedelta(days=self.session_days),
        )
        return self._store(token)

    def issue_agent_token(self, username: str, name: str, scope: Scope) -> tuple[FakeToken, str]:
        """An agent token owned by `username`; returns (token, bearer secret)."""
        user = self.user_by_name(username)
        token = FakeToken(
            id=f"tok_{secrets.token_hex(6)}",
            name=name,
            type=TokenType.AGENT,
            scope=scope,
            user_id=user.id,
            created_at=self.clock.now(),
        )
        secret = self._store(token)
        return token, secret

    def revoke(self, token_id: str) -> None:
        self.tokens[token_id].revoked = True

    def _store(self, token: FakeToken) -> str:
        secret = f"pb_{secrets.token_urlsafe(32)}"
        self.tokens[token.id] = token
        self.secrets[secret] = token.id
        return secret

    def public_token(self, token: FakeToken) -> Token:
        return Token(
            id=token.id,
            name=token.name,
            type=token.type,
            scope=token.scope,
            username=self.users[token.user_id].username,
            created_at=token.created_at,
            last_used_at=token.last_used_at,
            expires_at=token.expires_at,
        )

    def public_site(self, site: FakeSite) -> Site:
        return Site(
            slug=site.slug,
            name=site.name,
            url=site_url(self.public_sites_url, site.slug),
            created_at=site.created_at,
            file_count=len(site.files),
            bytes=sum(len(f.content) for f in site.files.values()),
        )

    def requests_for(self, token_id: str) -> list[RecordedRequest]:
        return [r for r in self.requests if r.token_id == token_id]


# ---------------------------------------------------------------------------
# Authorization


def _state(request: Request) -> FakeState:
    return request.app.state.fake


def _bearer(request: Request) -> str | None:
    header = request.headers.get("authorization", "")
    scheme, _, value = header.partition(" ")
    if scheme.lower() != "bearer" or not value.strip():
        return None
    return value.strip()


def _resolve_token(state: FakeState, request: Request) -> FakeToken | None:
    """The valid token behind the request's bearer, or None (missing, unknown, revoked, expired)."""
    secret = _bearer(request)
    if secret is None:
        return None
    token_id = state.secrets.get(secret)
    if token_id is None:
        return None
    token = state.tokens[token_id]
    now = state.clock.now()
    if token.revoked or (token.expires_at is not None and token.expires_at <= now):
        return None
    token.last_used_at = now
    if token.type is TokenType.SESSION:
        token.expires_at = now + timedelta(days=state.session_days)  # sliding expiry
    return token


def _authorize(request: Request, spec: RouteSpec) -> FakeToken | None:
    state = _state(request)
    token = _resolve_token(state, request)
    state.requests.append(
        RecordedRequest(spec.name, token.id if token else None, request.headers.get(VIA_HEADER))
    )
    if spec.access is Access.PUBLIC:
        return token
    if token is None:
        raise ApiError(401, ErrorCode.UNAUTHORIZED, "a valid bearer token is required")
    if not access_allows(spec.access, token.type, token.scope):
        if spec.access is Access.SESSION:
            raise ApiError(
                403,
                ErrorCode.SESSION_REQUIRED,
                "this route needs a signed-in user's session, not an agent token",
            )
        raise ApiError(
            403,
            ErrorCode.FORBIDDEN,
            f"this route needs the {spec.access.value} scope; the token has {token.scope.value}",
        )
    return token


def _requires(name: str) -> Callable[[Request], FakeToken | None]:
    spec = route(name)

    def dependency(request: Request) -> FakeToken | None:
        return _authorize(request, spec)

    return dependency


def _site(state: FakeState, slug: str) -> FakeSite:
    site = state.sites.get(slug)
    if site is None:
        raise ApiError(404, ErrorCode.NOT_FOUND, f"no site {slug!r}")
    return site


def _valid_path(raw: str, *, allow_root: bool = False) -> str:
    try:
        return validate_site_path(raw, allow_root=allow_root)
    except ValueError as e:
        raise ApiError(400, ErrorCode.INVALID_PATH, f"invalid path {raw!r}: {e}") from e


def _attachment(name: str) -> str:
    ascii_name = name.encode("ascii", "replace").decode("ascii").replace('"', "'")
    return f"attachment; filename=\"{ascii_name}\"; filename*=UTF-8''{quote(name)}"


# ---------------------------------------------------------------------------
# The app


def create_fake_backend(
    *,
    public_sites_url: str = DEFAULT_PUBLIC_SITES_URL,
    max_upload_mb: int = 200,
    session_days: int = 14,
    admin: tuple[str, str] | None = ("admin", "admin-password"),
) -> FastAPI:
    """Build a fresh fake backend. `admin` is the bootstrap account (username, password), or None for no account."""
    state = FakeState(
        public_sites_url=public_sites_url.rstrip("/"),
        max_upload_bytes=max_upload_mb * 1024 * 1024,
        session_days=session_days,
    )
    if admin is not None:
        state.add_user(*admin)

    app = FastAPI(title="paper-boxing fake backend", version=FAKE_VERSION, docs_url="/docs")
    app.state.fake = state
    install_error_handlers(app)

    # ---- ops ----

    @app.get("/health", response_model=Health)
    async def health() -> Health:
        return Health(ok=True, version=FAKE_VERSION, uptime_seconds=0.0)

    @app.get("/admin/version")
    async def admin_version() -> dict[str, str]:
        return {
            "name": "paper-boxing-fake-backend",
            "version": FAKE_VERSION,
            "public_sites_url": state.public_sites_url,
        }

    # ---- authentication ----

    @app.post(route("login").path, response_model=LoginResponse, status_code=200)
    async def login(
        body: LoginRequest, request: Request, _: Annotated[None, Depends(_requires("login"))]
    ) -> LoginResponse:
        try:
            user = state.user_by_name(body.username)
        except KeyError:
            user = None
        if user is None or user.password != body.password:
            raise ApiError(401, ErrorCode.INVALID_CREDENTIALS, "unknown username or wrong password")
        secret = state.issue_session(user.username)
        token = state.tokens[state.secrets[secret]]
        assert token.expires_at is not None
        return LoginResponse(token=secret, expires_at=token.expires_at, user=user.public())

    @app.post(route("logout").path, status_code=204)
    async def logout(token: Annotated[FakeToken, Depends(_requires("logout"))]) -> Response:
        token.revoked = True
        return Response(status_code=204)

    # ---- sites ----

    @app.get(route("list_sites").path, response_model=SiteList)
    async def list_sites(_: Annotated[FakeToken, Depends(_requires("list_sites"))]) -> SiteList:
        return SiteList(sites=[state.public_site(s) for _, s in sorted(state.sites.items())])

    @app.post(route("create_site").path, response_model=Site, status_code=201)
    async def create_site(
        body: CreateSiteRequest, _: Annotated[FakeToken, Depends(_requires("create_site"))]
    ) -> Site:
        try:
            slug = slug_from_name(body.name)
        except ValueError as e:
            raise ApiError(400, ErrorCode.INVALID_NAME, str(e)) from e
        if slug in state.sites:
            raise ApiError(409, ErrorCode.SITE_EXISTS, f"a site with the slug {slug!r} exists")
        site = FakeSite(slug=slug, name=body.name.strip(), created_at=state.clock.now())
        state.sites[slug] = site
        return state.public_site(site)

    @app.get(route("get_site").path, response_model=Site)
    async def get_site(slug: str, _: Annotated[FakeToken, Depends(_requires("get_site"))]) -> Site:
        return state.public_site(_site(state, slug))

    @app.delete(route("delete_site").path, status_code=204)
    async def delete_site(
        slug: str, _: Annotated[FakeToken, Depends(_requires("delete_site"))], confirm: str | None = None
    ) -> Response:
        site = _site(state, slug)
        if confirm != site.slug:
            raise ApiError(400, ErrorCode.CONFIRM_MISMATCH, f"pass ?confirm={site.slug} to delete the site")
        del state.sites[slug]
        return Response(status_code=204)

    # ---- files and folders ----

    @app.get(route("list_files").path, response_model=FileListing)
    async def list_files(
        slug: str, _: Annotated[FakeToken, Depends(_requires("list_files"))], path: str = ""
    ) -> FileListing:
        site = _site(state, slug)
        folder = _valid_path(path, allow_root=True)
        if folder in site.files:
            raise ApiError(409, ErrorCode.NOT_A_FOLDER, f"{folder!r} is a file")
        if not site.has_folder(folder):
            raise ApiError(404, ErrorCode.NOT_FOUND, f"no folder {folder!r} in site {slug!r}")
        subfolders, files = site.children(folder)
        entries = [
            FileEntry(
                name=path_name(p),
                type=EntryType.FOLDER,
                bytes=site.folder_bytes(p),
                modified_at=site.folder_modified_at(p),
                sha256=None,
            )
            for p in subfolders
        ] + [
            FileEntry(
                name=path_name(p),
                type=EntryType.FILE,
                bytes=len(site.files[p].content),
                modified_at=site.files[p].modified_at,
                sha256=site.files[p].sha256,
            )
            for p in files
        ]
        return FileListing(site=slug, path=folder, entries=entries)

    def _check_write_target(site: FakeSite, path: str, *, overwrite: bool) -> bool:
        """Validate a write to `path`; return whether it replaces an existing file."""
        if path in site.folders:
            raise ApiError(409, ErrorCode.NOT_A_FILE, f"{path!r} is a folder")
        parent = path_parent(path)
        while parent:
            if parent in site.files:
                raise ApiError(409, ErrorCode.NOT_A_FOLDER, f"{parent!r} is a file, not a folder")
            parent = path_parent(parent)
        if path in site.files:
            if not overwrite:
                raise ApiError(
                    409, ErrorCode.FILE_EXISTS, f"{path!r} exists; pass overwrite=true to replace it"
                )
            return True
        return False

    def _write(site: FakeSite, path: str, content: bytes) -> UploadResult:
        now = state.clock.now()
        replaced = path in site.files
        site.add_folders_for(path, now)
        site.files[path] = FakeFile(content=content, modified_at=now)
        return UploadResult(path=path, bytes=len(content), sha256=site.files[path].sha256, replaced=replaced)

    @app.put(route("upload_file").path, response_model=UploadResult)
    async def upload_file(
        slug: str,
        path: str,
        request: Request,
        _: Annotated[FakeToken, Depends(_requires("upload_file"))],
        overwrite: bool = False,
    ) -> JSONResponse:
        site = _site(state, slug)
        target = _valid_path(path)
        content = await request.body()
        if len(content) > state.max_upload_bytes:
            raise ApiError(
                413,
                ErrorCode.PAYLOAD_TOO_LARGE,
                f"the file exceeds the cap of {state.max_upload_bytes} bytes",
            )
        replacing = _check_write_target(site, target, overwrite=overwrite)
        result = _write(site, target, content)
        return JSONResponse(result.model_dump(mode="json"), status_code=200 if replacing else 201)

    @app.get(route("download_file").path)
    async def download_file(
        slug: str, path: str, _: Annotated[FakeToken, Depends(_requires("download_file"))]
    ) -> Response:
        site = _site(state, slug)
        target = _valid_path(path)
        if target in site.folders:
            raise ApiError(409, ErrorCode.NOT_A_FILE, f"{target!r} is a folder, not a file")
        entry = site.files.get(target)
        if entry is None:
            raise ApiError(404, ErrorCode.NOT_FOUND, f"no file {target!r} in site {slug!r}")
        content_type = mimetypes.guess_type(target)[0] or "application/octet-stream"
        return Response(
            content=entry.content,
            media_type=content_type,
            headers={"Content-Disposition": _attachment(path_name(target))},
        )

    @app.delete(route("delete_file").path, status_code=204)
    async def delete_file(
        slug: str, path: str, _: Annotated[FakeToken, Depends(_requires("delete_file"))]
    ) -> Response:
        site = _site(state, slug)
        target = _valid_path(path)
        if target in site.folders:
            raise ApiError(409, ErrorCode.NOT_A_FILE, f"{target!r} is a folder; use the folders route")
        if target not in site.files:
            raise ApiError(404, ErrorCode.NOT_FOUND, f"no file {target!r} in site {slug!r}")
        del site.files[target]
        return Response(status_code=204)

    @app.delete(route("delete_folder").path, status_code=204)
    async def delete_folder(
        slug: str,
        path: str,
        _: Annotated[FakeToken, Depends(_requires("delete_folder"))],
        recursive: bool = False,
    ) -> Response:
        site = _site(state, slug)
        target = _valid_path(path)
        if target in site.files:
            raise ApiError(409, ErrorCode.NOT_A_FOLDER, f"{target!r} is a file; use the files route")
        if target not in site.folders:
            raise ApiError(404, ErrorCode.NOT_FOUND, f"no folder {target!r} in site {slug!r}")
        subfolders, files = site.descendants(target)
        if (subfolders or files) and not recursive:
            raise ApiError(
                409,
                ErrorCode.FOLDER_NOT_EMPTY,
                f"{target!r} is not empty; pass recursive=true to delete everything in it",
            )
        for p in files:
            del site.files[p]
        for p in subfolders:
            del site.folders[p]
        del site.folders[target]
        return Response(status_code=204)

    # ---- access tokens ----

    @app.get(route("list_tokens").path, response_model=TokenList)
    async def list_tokens(_: Annotated[FakeToken, Depends(_requires("list_tokens"))]) -> TokenList:
        agents = [t for t in state.tokens.values() if t.type is TokenType.AGENT and not t.revoked]
        return TokenList(
            tokens=[state.public_token(t) for t in sorted(agents, key=lambda t: (t.created_at, t.id))]
        )

    @app.post(route("create_token").path, response_model=TokenCreated, status_code=201)
    async def create_token(
        body: CreateTokenRequest, caller: Annotated[FakeToken, Depends(_requires("create_token"))]
    ) -> TokenCreated:
        owner = state.users[caller.user_id]
        token, secret = state.issue_agent_token(owner.username, body.name.strip(), body.scope)
        return TokenCreated(token=state.public_token(token), secret=secret)

    @app.get(route("token_self").path, response_model=TokenSelf)
    async def token_self(caller: Annotated[FakeToken, Depends(_requires("token_self"))]) -> TokenSelf:
        return TokenSelf(
            id=caller.id,
            name=caller.name,
            type=caller.type,
            scope=caller.scope,
            username=state.users[caller.user_id].username,
            expires_at=caller.expires_at,
        )

    @app.delete(route("revoke_token").path, status_code=204)
    async def revoke_token(
        token_id: str, _: Annotated[FakeToken, Depends(_requires("revoke_token"))]
    ) -> Response:
        token = state.tokens.get(token_id)
        if token is None or token.type is not TokenType.AGENT or token.revoked:
            raise ApiError(404, ErrorCode.NOT_FOUND, f"no agent token {token_id!r}")
        token.revoked = True
        return Response(status_code=204)

    # ---- users ----

    @app.get(route("list_users").path, response_model=UserList)
    async def list_users(_: Annotated[FakeToken, Depends(_requires("list_users"))]) -> UserList:
        return UserList(users=[u.public() for u in sorted(state.users.values(), key=lambda u: u.username)])

    @app.post(route("create_user").path, response_model=User, status_code=201)
    async def create_user(
        body: CreateUserRequest, _: Annotated[FakeToken, Depends(_requires("create_user"))]
    ) -> User:
        try:
            user = state.add_user(body.username, body.password)
        except ValueError as e:
            raise ApiError(409, ErrorCode.USER_EXISTS, f"the username {body.username!r} is taken") from e
        return user.public()

    @app.post(route("change_password").path, status_code=204)
    async def change_password(
        body: ChangePasswordRequest, caller: Annotated[FakeToken, Depends(_requires("change_password"))]
    ) -> Response:
        user = state.users[caller.user_id]
        if body.current != user.password:
            raise ApiError(403, ErrorCode.WRONG_PASSWORD, "the current password is wrong")
        user.password = body.new
        for token in state.tokens.values():
            if token.user_id == user.id and token.type is TokenType.SESSION and token.id != caller.id:
                token.revoked = True  # the change signs out the user's other sessions
        return Response(status_code=204)

    @app.delete(route("delete_user").path, status_code=204)
    async def delete_user(
        user_id: str, _: Annotated[FakeToken, Depends(_requires("delete_user"))]
    ) -> Response:
        if user_id not in state.users:
            raise ApiError(404, ErrorCode.NOT_FOUND, f"no user {user_id!r}")
        if len(state.users) == 1:
            raise ApiError(409, ErrorCode.LAST_USER, "the last account cannot be deleted")
        del state.users[user_id]
        for token in state.tokens.values():
            if token.user_id == user_id:
                token.revoked = True  # every token of a removed account dies with it
        return Response(status_code=204)

    return app
