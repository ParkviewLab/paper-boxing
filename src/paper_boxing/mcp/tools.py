# SPDX-FileCopyrightText: 2026 Gary Frattarola <garyf@parkviewlab.ai>
#
# SPDX-License-Identifier: MIT OR Apache-2.0

"""The MCP tool specifications: name, title, description, input and output
schema, annotations and the scope a token needs (docs/api.md, "MCP tools").

The scaffold fixes the specifications; the handlers and the dispatch that
run them against the backend are the MCP worker's (docs/design.md section
9). A handler receives the validated arguments of its input model and returns
its output model, so the SDK's output validation passes by construction.
"""

from __future__ import annotations

from dataclasses import dataclass

from mcp import types
from pydantic import BaseModel

from paper_boxing.common.scopes import Scope, scope_allows
from paper_boxing.mcp import schema

REST_NOTE = (
    "Files larger than the cap go through the backend's REST API with the same token: "
    "PUT /api/v1/sites/{site}/files/{path} to upload and GET the same path to download."
)


@dataclass(frozen=True)
class ToolSpec:
    """One MCP tool: its public specification and the scope it requires."""

    name: str
    title: str
    description: str
    input_model: type[BaseModel]
    output_model: type[BaseModel]
    scope: Scope
    annotations: types.ToolAnnotations

    def to_tool(self) -> types.Tool:
        return types.Tool(
            name=self.name,
            title=self.title,
            description=self.description,
            inputSchema=self.input_model.model_json_schema(),
            outputSchema=self.output_model.model_json_schema(),
            annotations=self.annotations,
        )


def _read_only() -> types.ToolAnnotations:
    return types.ToolAnnotations(
        readOnlyHint=True, destructiveHint=False, idempotentHint=True, openWorldHint=False
    )


def _additive(*, idempotent: bool) -> types.ToolAnnotations:
    return types.ToolAnnotations(
        readOnlyHint=False, destructiveHint=False, idempotentHint=idempotent, openWorldHint=False
    )


def _destructive() -> types.ToolAnnotations:
    return types.ToolAnnotations(
        readOnlyHint=False, destructiveHint=True, idempotentHint=True, openWorldHint=False
    )


def tool_specs(max_file_mb: int) -> tuple[ToolSpec, ...]:
    """The eight tools, with the configured file-size cap written into the descriptions."""
    cap = f"{max_file_mb} MiB"
    return (
        ToolSpec(
            name="list_sites",
            title="List sites",
            description="List every site: slug, display name, served URL, creation time, file count and bytes.",
            input_model=schema.ListSitesInput,
            output_model=schema.ListSitesOutput,
            scope=Scope.READ_ONLY,
            annotations=_read_only(),
        ),
        ToolSpec(
            name="create_site",
            title="Create site",
            description=(
                "Create a site from a display name. The slug is derived from the name (lowercase letters, "
                "digits, hyphens), must be unique, and never changes afterwards. Returns the site, including "
                "the URL at which its files are served."
            ),
            input_model=schema.CreateSiteInput,
            output_model=schema.Site,
            scope=Scope.READ_WRITE,
            annotations=_additive(idempotent=False),
        ),
        ToolSpec(
            name="list_files",
            title="List files",
            description=(
                "List one folder of a site: for each entry its name, type (file or folder), bytes, "
                "modification time and, for a file, its sha256. Folders come first, then files, by name."
            ),
            input_model=schema.ListFilesInput,
            output_model=schema.FileListing,
            scope=Scope.READ_ONLY,
            annotations=_read_only(),
        ),
        ToolSpec(
            name="upload_file",
            title="Upload file",
            description=(
                f"Upload one file (base64 content, at most {cap} decoded) to a path inside a site, creating "
                "intermediate folders. An existing file is refused unless overwrite is true; a replacement is "
                f"atomic. Returns the path, bytes and sha256 written. {REST_NOTE}"
            ),
            input_model=schema.UploadFileInput,
            output_model=schema.UploadResult,
            scope=Scope.READ_WRITE,
            annotations=_additive(idempotent=True),
        ),
        ToolSpec(
            name="download_file",
            title="Download file",
            description=(
                f"Download one file (at most {cap}) as base64, with its sha256 and content type. {REST_NOTE}"
            ),
            input_model=schema.DownloadFileInput,
            output_model=schema.DownloadFileOutput,
            scope=Scope.READ_ONLY,
            annotations=_read_only(),
        ),
        ToolSpec(
            name="delete_file",
            title="Delete file",
            description="Delete one file. A folder at the path is refused; use delete_folder.",
            input_model=schema.DeleteFileInput,
            output_model=schema.DeletedFileOutput,
            scope=Scope.REMOVE_DESTRUCTIVE,
            annotations=_destructive(),
        ),
        ToolSpec(
            name="delete_folder",
            title="Delete folder",
            description=(
                "Delete a folder. A non-empty folder is refused unless recursive is true, in which case "
                "everything under it is deleted. The site root cannot be deleted this way; use delete_site."
            ),
            input_model=schema.DeleteFolderInput,
            output_model=schema.DeletedFileOutput,
            scope=Scope.REMOVE_DESTRUCTIVE,
            annotations=_destructive(),
        ),
        ToolSpec(
            name="delete_site",
            title="Delete site",
            description=(
                "Delete a whole site and every file in it. Refused unless confirm equals the site's slug."
            ),
            input_model=schema.DeleteSiteInput,
            output_model=schema.DeletedSiteOutput,
            scope=Scope.REMOVE_DESTRUCTIVE,
            annotations=_destructive(),
        ),
    )


TOOL_NAMES: tuple[str, ...] = tuple(spec.name for spec in tool_specs(8))


def list_tools(specs: tuple[ToolSpec, ...], scope: Scope) -> list[types.Tool]:
    """The tools a token of `scope` may see and call, in table order."""
    return [spec.to_tool() for spec in specs if scope_allows(scope, spec.scope)]


def spec_for(specs: tuple[ToolSpec, ...], name: str) -> ToolSpec:
    """Look a tool up by name; raises `KeyError` for an unknown one."""
    for spec in specs:
        if spec.name == name:
            return spec
    raise KeyError(name)
