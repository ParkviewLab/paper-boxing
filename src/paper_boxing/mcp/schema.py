# SPDX-FileCopyrightText: 2026 Gary Frattarola <garyf@parkviewlab.ai>
#
# SPDX-License-Identifier: MIT OR Apache-2.0

"""Pydantic models at the MCP tool boundary: one input model and one output
model per tool. Their JSON schemas become each tool's `inputSchema` and
`outputSchema` (see `tools.py`), so a tool's arguments and its structured
result are validated by the SDK on both sides of the call.

Outputs reuse the REST contract's models where the tool returns what the
backend returned (a site, a listing, an upload result).
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from paper_boxing.common.schema import FileListing, Site, UploadResult

__all__ = [
    "CreateSiteInput",
    "DeleteFileInput",
    "DeleteFolderInput",
    "DeleteSiteInput",
    "DeletedFileOutput",
    "DeletedSiteOutput",
    "DownloadFileInput",
    "DownloadFileOutput",
    "FileListing",
    "ListFilesInput",
    "ListSitesInput",
    "ListSitesOutput",
    "Site",
    "UploadFileInput",
    "UploadResult",
]

_SITE = Field(description="the site's slug (lowercase letters, digits and hyphens)")
_PATH = Field(description="path inside the site, '/'-separated and relative, for example 'docs/index.html'")

# ---- inputs ----


class ToolInput(BaseModel):
    """An argument the tool does not know is refused, not dropped: a misspelling never turns into a default."""

    model_config = ConfigDict(extra="forbid")


class ListSitesInput(ToolInput):
    pass


class CreateSiteInput(ToolInput):
    name: str = Field(min_length=1, max_length=120, description="display name; the slug is derived from it")


class ListFilesInput(ToolInput):
    site: str = _SITE
    path: str = Field(default="", description="the folder to list; '' is the site root")


class UploadFileInput(ToolInput):
    site: str = _SITE
    path: str = _PATH
    content_base64: str = Field(description="the file's bytes, base64-encoded")
    overwrite: bool = Field(
        default=False, description="replace an existing file; without it an existing file is refused"
    )


class DownloadFileInput(ToolInput):
    site: str = _SITE
    path: str = _PATH


class DeleteFileInput(ToolInput):
    site: str = _SITE
    path: str = _PATH


class DeleteFolderInput(ToolInput):
    site: str = _SITE
    path: str = _PATH
    recursive: bool = Field(default=False, description="delete a non-empty folder with everything in it")


class DeleteSiteInput(ToolInput):
    site: str = _SITE
    confirm: str = Field(description="must equal the slug; the tool refuses otherwise")


# ---- outputs ----


class ListSitesOutput(BaseModel):
    sites: list[Site]


class DownloadFileOutput(BaseModel):
    site: str
    path: str
    bytes: int
    sha256: str
    content_type: str
    content_base64: str


class DeletedFileOutput(BaseModel):
    site: str
    path: str
    deleted: Literal[True] = True


class DeletedSiteOutput(BaseModel):
    slug: str
    deleted: Literal[True] = True
