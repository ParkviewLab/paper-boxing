# SPDX-FileCopyrightText: 2026 Gary Frattarola <garyf@parkviewlab.ai>
#
# SPDX-License-Identifier: MIT OR Apache-2.0

"""The REST client of the backend, used by the frontend and by the MCP server.

Stateless with respect to the caller: every method takes the bearer `token`
explicitly, so one shared `httpx.AsyncClient` serves every user and every
request (the frontend holds one in its lifespan, the MCP server one per
process). A test injects a fake backend by building the httpx client with
`httpx.ASGITransport(app=create_fake_backend())`.

Every non-2xx answer raises `BackendError` carrying the contract's error code;
a connection failure raises `BackendUnreachable`.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from typing import Any

import httpx
from pydantic import ValidationError

from paper_boxing.common.routes import VIA_HEADER, build_path
from paper_boxing.common.schema import (
    ChangePasswordRequest,
    CreateSiteRequest,
    CreateTokenRequest,
    CreateUserRequest,
    ErrorBody,
    ErrorCode,
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
from paper_boxing.common.scopes import Scope


class BackendError(Exception):
    """A non-2xx answer from the backend, with the contract's `code` and `message`."""

    def __init__(self, status: int, code: str, message: str) -> None:
        super().__init__(f"{status} {code}: {message}")
        self.status = status
        self.code = code
        self.message = message


class BackendUnreachable(BackendError):
    """The backend could not be reached at all (connection refused, timeout, DNS)."""

    def __init__(self, message: str) -> None:
        super().__init__(503, "backend_unreachable", message)


@dataclass(frozen=True)
class DownloadedFile:
    path: str
    content: bytes
    content_type: str
    sha256: str


class BackendClient:
    """Typed access to every route in `paper_boxing.common.routes.ROUTES`."""

    def __init__(self, http: httpx.AsyncClient, *, via: str | None = None) -> None:
        self._http = http
        self._via = via

    @classmethod
    def for_base_url(cls, base_url: str, *, via: str | None = None, timeout: float = 60.0) -> BackendClient:
        return cls(httpx.AsyncClient(base_url=base_url, timeout=timeout), via=via)

    async def aclose(self) -> None:
        await self._http.aclose()

    # ---- plumbing ----

    def _headers(self, token: str | None) -> dict[str, str]:
        headers: dict[str, str] = {}
        if token is not None:
            headers["Authorization"] = f"Bearer {token}"
        if self._via:
            headers[VIA_HEADER] = self._via
        return headers

    async def _request(
        self,
        method: str,
        path: str,
        token: str | None,
        *,
        json: dict[str, Any] | None = None,
        content: bytes | None = None,
        params: dict[str, str] | None = None,
        files: list[tuple[str, tuple[str, bytes, str]]] | None = None,
    ) -> httpx.Response:
        try:
            response = await self._http.request(
                method,
                path,
                headers=self._headers(token),
                json=json,
                content=content,
                params=params,
                files=files,
            )
        except httpx.TransportError as e:
            raise BackendUnreachable(f"{method} {path}: {e}") from e
        if response.is_success:
            return response
        raise _error_from(response)

    # ---- ops ----

    async def health(self) -> Health:
        return Health.model_validate((await self._request("GET", "/health", None)).json())

    # ---- authentication ----

    async def login(self, username: str, password: str) -> LoginResponse:
        body = LoginRequest(username=username, password=password).model_dump()
        return LoginResponse.model_validate(
            (await self._request("POST", build_path("login"), None, json=body)).json()
        )

    async def logout(self, token: str) -> None:
        await self._request("POST", build_path("logout"), token)

    # ---- sites ----

    async def list_sites(self, token: str) -> list[Site]:
        return SiteList.model_validate(
            (await self._request("GET", build_path("list_sites"), token)).json()
        ).sites

    async def get_site(self, token: str, slug: str) -> Site:
        return Site.model_validate(
            (await self._request("GET", build_path("get_site", slug=slug), token)).json()
        )

    async def create_site(self, token: str, name: str) -> Site:
        body = CreateSiteRequest(name=name).model_dump()
        return Site.model_validate(
            (await self._request("POST", build_path("create_site"), token, json=body)).json()
        )

    async def delete_site(self, token: str, slug: str, confirm: str) -> None:
        await self._request(
            "DELETE", build_path("delete_site", slug=slug), token, params={"confirm": confirm}
        )

    # ---- files and folders ----

    async def list_files(self, token: str, slug: str, path: str = "") -> FileListing:
        response = await self._request(
            "GET", build_path("list_files", slug=slug), token, params={"path": path}
        )
        return FileListing.model_validate(response.json())

    async def upload_file(
        self, token: str, slug: str, path: str, content: bytes, *, overwrite: bool = False
    ) -> UploadResult:
        response = await self._request(
            "PUT",
            build_path("upload_file", slug=slug, path=path),
            token,
            content=content,
            params={"overwrite": "true" if overwrite else "false"},
        )
        return UploadResult.model_validate(response.json())

    async def download_file(self, token: str, slug: str, path: str) -> DownloadedFile:
        response = await self._request("GET", build_path("download_file", slug=slug, path=path), token)
        content = response.content
        return DownloadedFile(
            path=path,
            content=content,
            content_type=response.headers.get("content-type", "application/octet-stream"),
            sha256=hashlib.sha256(content).hexdigest(),
        )

    async def delete_file(self, token: str, slug: str, path: str) -> None:
        await self._request("DELETE", build_path("delete_file", slug=slug, path=path), token)

    async def delete_folder(self, token: str, slug: str, path: str, *, recursive: bool = False) -> None:
        await self._request(
            "DELETE",
            build_path("delete_folder", slug=slug, path=path),
            token,
            params={"recursive": "true" if recursive else "false"},
        )

    # ---- access tokens ----

    async def list_tokens(self, token: str) -> list[Token]:
        return TokenList.model_validate(
            (await self._request("GET", build_path("list_tokens"), token)).json()
        ).tokens

    async def create_token(self, token: str, name: str, scope: Scope) -> TokenCreated:
        body = CreateTokenRequest(name=name, scope=scope).model_dump(mode="json")
        return TokenCreated.model_validate(
            (await self._request("POST", build_path("create_token"), token, json=body)).json()
        )

    async def revoke_token(self, token: str, token_id: str) -> None:
        await self._request("DELETE", build_path("revoke_token", token_id=token_id), token)

    async def token_self(self, token: str) -> TokenSelf:
        return TokenSelf.model_validate((await self._request("GET", build_path("token_self"), token)).json())

    # ---- users ----

    async def list_users(self, token: str) -> list[User]:
        return UserList.model_validate(
            (await self._request("GET", build_path("list_users"), token)).json()
        ).users

    async def create_user(self, token: str, username: str, password: str) -> User:
        body = CreateUserRequest(username=username, password=password).model_dump()
        return User.model_validate(
            (await self._request("POST", build_path("create_user"), token, json=body)).json()
        )

    async def delete_user(self, token: str, user_id: str) -> None:
        await self._request("DELETE", build_path("delete_user", user_id=user_id), token)

    async def change_password(self, token: str, current: str, new: str) -> None:
        body = ChangePasswordRequest(current=current, new=new).model_dump()
        await self._request("POST", build_path("change_password"), token, json=body)


def _error_from(response: httpx.Response) -> BackendError:
    """Build a `BackendError` from a non-2xx response; a body outside the contract gets a generic code."""
    try:
        body = ErrorBody.model_validate(response.json())
    except (ValueError, ValidationError):
        text = response.text.strip()
        return BackendError(
            response.status_code, ErrorCode.INTERNAL_ERROR.value, text[:500] or response.reason_phrase
        )
    return BackendError(response.status_code, body.error.code.value, body.error.message)
