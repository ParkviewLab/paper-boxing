# SPDX-FileCopyrightText: 2026 Gary Frattarola <garyf@parkviewlab.ai>
#
# SPDX-License-Identifier: MIT OR Apache-2.0

"""Pydantic models of the REST contract (docs/api.md).

Every request and response body of the backend's API is one of these models.
The backend produces them, the fake backend produces them identically, and the
REST client parses them; the MCP server reuses several as tool outputs.
"""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum

from pydantic import BaseModel, Field

from paper_boxing.common.scopes import Scope, TokenType

# ---- ops endpoints (outside /api/v1, no authentication) ----


class Health(BaseModel):
    ok: bool
    version: str
    uptime_seconds: float


# ---- errors ----


class ErrorCode(StrEnum):
    """Every error the backend can answer with, paired with its HTTP status in docs/api.md."""

    # 400
    INVALID_NAME = "invalid_name"
    INVALID_PATH = "invalid_path"
    CONFIRM_MISMATCH = "confirm_mismatch"
    BAD_REQUEST = "bad_request"
    # 401
    UNAUTHORIZED = "unauthorized"
    INVALID_CREDENTIALS = "invalid_credentials"
    # 403
    FORBIDDEN = "forbidden"
    SESSION_REQUIRED = "session_required"
    WRONG_PASSWORD = "wrong_password"
    WRONG_TOKEN_TYPE = "wrong_token_type"
    # 404
    NOT_FOUND = "not_found"
    # 405
    METHOD_NOT_ALLOWED = "method_not_allowed"
    # 409
    SITE_EXISTS = "site_exists"
    FILE_EXISTS = "file_exists"
    FOLDER_NOT_EMPTY = "folder_not_empty"
    NOT_A_FILE = "not_a_file"
    NOT_A_FOLDER = "not_a_folder"
    USER_EXISTS = "user_exists"
    LAST_USER = "last_user"
    # 413
    PAYLOAD_TOO_LARGE = "payload_too_large"
    # 422
    VALIDATION_ERROR = "validation_error"
    # 500 / 507
    INTERNAL_ERROR = "internal_error"
    INSUFFICIENT_STORAGE = "insufficient_storage"
    # 503, the MCP server's own: the backend could not be reached, or did not confirm a token as the contract says
    BACKEND_UNREACHABLE = "backend_unreachable"


class ErrorDetail(BaseModel):
    code: ErrorCode
    message: str


class ErrorBody(BaseModel):
    """The body of every error response: `{"error": {"code": ..., "message": ...}}`."""

    error: ErrorDetail


# ---- authentication ----


class LoginRequest(BaseModel):
    username: str = Field(min_length=1, max_length=64)
    password: str = Field(min_length=1)


class User(BaseModel):
    id: str
    username: str
    created_at: datetime


class LoginResponse(BaseModel):
    token: str = Field(description="the session token; send it as `Authorization: Bearer <token>`")
    expires_at: datetime = Field(description="sliding expiry; every authenticated request extends it")
    user: User


# ---- sites ----


class Site(BaseModel):
    slug: str
    name: str
    url: str = Field(description="the served address, `<PAPER_BOXING_PUBLIC_SITES_URL>/<slug>/`")
    created_at: datetime
    file_count: int
    bytes: int


class SiteList(BaseModel):
    sites: list[Site]


class CreateSiteRequest(BaseModel):
    name: str = Field(min_length=1, max_length=120, description="display name; the slug is derived from it")


# ---- files and folders ----


class EntryType(StrEnum):
    FILE = "file"
    FOLDER = "folder"


class FileEntry(BaseModel):
    name: str
    type: EntryType
    bytes: int = Field(description="the file's size, or a folder's recursive total")
    modified_at: datetime
    sha256: str | None = Field(default=None, description="hex digest for a file; null for a folder")


class FileListing(BaseModel):
    site: str
    path: str = Field(description='the listed folder, canonical; "" for the site root')
    entries: list[FileEntry] = Field(description="folders first, then files, each sorted by name")


class UploadResult(BaseModel):
    path: str
    bytes: int
    sha256: str
    replaced: bool = Field(description="true when an existing file was overwritten")


# ---- access tokens ----


class Token(BaseModel):
    id: str
    name: str
    type: TokenType
    scope: Scope
    username: str = Field(description="the owner: the user who created it")
    created_at: datetime
    last_used_at: datetime | None
    expires_at: datetime | None = Field(description="null for an agent token; agent tokens do not expire")


class TokenList(BaseModel):
    tokens: list[Token]


class CreateTokenRequest(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    scope: Scope


class TokenCreated(BaseModel):
    token: Token
    secret: str = Field(description="the bearer secret, shown exactly once")


class TokenSelf(BaseModel):
    id: str
    name: str
    type: TokenType
    scope: Scope
    username: str
    expires_at: datetime | None


# ---- users ----


class UserList(BaseModel):
    users: list[User]


class CreateUserRequest(BaseModel):
    username: str = Field(min_length=1, max_length=64, pattern=r"^[A-Za-z0-9][A-Za-z0-9._-]*$")
    password: str = Field(min_length=8)


class ChangePasswordRequest(BaseModel):
    current: str = Field(min_length=1)
    new: str = Field(min_length=8)
