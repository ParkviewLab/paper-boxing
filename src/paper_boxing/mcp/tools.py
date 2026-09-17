# SPDX-FileCopyrightText: 2026 Gary Frattarola <garyf@parkviewlab.ai>
#
# SPDX-License-Identifier: MIT OR Apache-2.0

"""The MCP tools: their specifications (name, title, description, input and
output schema, annotations, the scope a token needs and the handler that runs
them; docs/api.md, "MCP tools"), and the dispatch.

A handler receives the validated arguments of its input model and returns its
output model, so the SDK's output validation passes by construction. Every
failure is a `ToolError` carrying one of the contract's error codes (the
backend's own when the backend refused) and a message, which the server turns
into a tool result with `isError` rather than an exception the client cannot
read. The handlers validate a slug or a path before calling, with the
contract's shared rules, so an impossible name costs no round trip; the
backend enforces every rule again.
"""

from __future__ import annotations

import base64
import binascii
import json
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any

from mcp import types
from pydantic import BaseModel, ValidationError

from paper_boxing.common.client import BackendClient, BackendError
from paper_boxing.common.naming import path_name, path_parent, validate_site_path, validate_slug
from paper_boxing.common.schema import EntryType, ErrorCode
from paper_boxing.common.scopes import Scope, scope_allows
from paper_boxing.mcp import schema

REST_NOTE = (
    "Files larger than the cap go through the backend's REST API with the same token: "
    "PUT /api/v1/sites/{site}/files/{path} to upload and GET the same path to download."
)


class ToolError(Exception):
    """A tool's failure, reported to the client as a tool result with `isError`.

    `code` is one of the contract's error codes (the backend's own when the
    backend refused); an unknown code is refused here, where it is raised.
    `message` says what happened and, where there is one, what to do instead.
    """

    def __init__(self, code: ErrorCode | str, message: str) -> None:
        self.code = ErrorCode(code)
        self.message = message
        super().__init__(f"{self.code.value}: {message}")

    @classmethod
    def from_backend(cls, error: BackendError) -> ToolError:
        return cls(error.code, error.message)

    def body(self) -> dict[str, Any]:
        return {"error": {"code": self.code.value, "message": self.message}}

    def result(self) -> types.CallToolResult:
        """The error as the SDK's tool result: `isError` with the contract's error body as JSON text."""
        return types.CallToolResult(
            content=[types.TextContent(type="text", text=json.dumps(self.body(), indent=2))],
            isError=True,
        )


@dataclass(frozen=True)
class ToolContext:
    """What a handler needs for one call: the backend, the confirmed bearer to forward, and the cap."""

    backend: BackendClient
    bearer: str
    max_file_bytes: int

    @property
    def max_file_mb(self) -> int:
        return self.max_file_bytes // (1024 * 1024)


Handler = Callable[[ToolContext, Any], Awaitable[BaseModel]]


@dataclass(frozen=True)
class ToolSpec:
    """One MCP tool: its public specification, the scope it requires, and the handler that runs it."""

    name: str
    title: str
    description: str
    input_model: type[BaseModel]
    output_model: type[BaseModel]
    scope: Scope
    annotations: types.ToolAnnotations
    handler: Handler

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


def _additive(*, idempotent: bool | None) -> types.ToolAnnotations:
    """An additive tool; `idempotent=None` leaves the hint unset where the answer depends on the arguments."""
    return types.ToolAnnotations(
        readOnlyHint=False, destructiveHint=False, idempotentHint=idempotent, openWorldHint=False
    )


def _destructive() -> types.ToolAnnotations:
    return types.ToolAnnotations(
        readOnlyHint=False, destructiveHint=True, idempotentHint=True, openWorldHint=False
    )


# ---------------------------------------------------------------------------
# Handlers


def _slug(site: str) -> str:
    try:
        return validate_slug(site)
    except ValueError as e:
        raise ToolError(ErrorCode.NOT_FOUND, f"no site {site!r}: {e}") from e


def _path(path: str, *, allow_root: bool = False) -> str:
    try:
        return validate_site_path(path, allow_root=allow_root)
    except ValueError as e:
        raise ToolError(ErrorCode.INVALID_PATH, f"invalid path {path!r}: {e}") from e


def _decode(content_base64: str) -> bytes:
    compact = "".join(content_base64.split())
    try:
        return base64.b64decode(compact, validate=True)
    except (binascii.Error, ValueError) as e:
        raise ToolError(
            ErrorCode.VALIDATION_ERROR, f"content_base64 is not valid standard base64: {e}"
        ) from e


def _too_large(ctx: ToolContext, size: int, verb: str, site: str, path: str) -> ToolError:
    return ToolError(
        ErrorCode.PAYLOAD_TOO_LARGE,
        f"the file is {size} bytes; a tool call carries at most {ctx.max_file_mb} MiB "
        f"({ctx.max_file_bytes} bytes). {verb} it with "
        f"{'PUT' if verb == 'Upload' else 'GET'} /api/v1/sites/{site}/files/{path} "
        "on the backend's REST API, with the same token"
        + (" as the raw request body." if verb == "Upload" else "."),
    )


async def list_sites(ctx: ToolContext, args: schema.ListSitesInput) -> schema.ListSitesOutput:
    return schema.ListSitesOutput(sites=await ctx.backend.list_sites(ctx.bearer))


async def create_site(ctx: ToolContext, args: schema.CreateSiteInput) -> schema.Site:
    return await ctx.backend.create_site(ctx.bearer, args.name)


async def list_files(ctx: ToolContext, args: schema.ListFilesInput) -> schema.FileListing:
    return await ctx.backend.list_files(ctx.bearer, _slug(args.site), _path(args.path, allow_root=True))


async def upload_file(ctx: ToolContext, args: schema.UploadFileInput) -> schema.UploadResult:
    site, path = _slug(args.site), _path(args.path)
    content = _decode(args.content_base64)
    if len(content) > ctx.max_file_bytes:
        raise _too_large(ctx, len(content), "Upload", site, path)
    return await ctx.backend.upload_file(ctx.bearer, site, path, content, overwrite=args.overwrite)


async def download_file(ctx: ToolContext, args: schema.DownloadFileInput) -> schema.DownloadFileOutput:
    site, path = _slug(args.site), _path(args.path)
    # The parent folder's listing gives the size, so a file above the cap is refused before its bytes
    # travel; the check on the fetched bytes stays, since the file may change in between. A missing
    # or folder entry is left to the fetch, so the backend's own 404 or 409 is what comes back.
    listing = await ctx.backend.list_files(ctx.bearer, site, path_parent(path))
    entry = next((e for e in listing.entries if e.name == path_name(path)), None)
    if entry is not None and entry.type is EntryType.FILE and entry.bytes > ctx.max_file_bytes:
        raise _too_large(ctx, entry.bytes, "Download", site, path)
    downloaded = await ctx.backend.download_file(ctx.bearer, site, path)
    if len(downloaded.content) > ctx.max_file_bytes:
        raise _too_large(ctx, len(downloaded.content), "Download", site, path)
    return schema.DownloadFileOutput(
        site=site,
        path=path,
        bytes=len(downloaded.content),
        sha256=downloaded.sha256,
        content_type=downloaded.content_type,
        content_base64=base64.b64encode(downloaded.content).decode("ascii"),
    )


async def delete_file(ctx: ToolContext, args: schema.DeleteFileInput) -> schema.DeletedFileOutput:
    site, path = _slug(args.site), _path(args.path)
    await ctx.backend.delete_file(ctx.bearer, site, path)
    return schema.DeletedFileOutput(site=site, path=path)


async def delete_folder(ctx: ToolContext, args: schema.DeleteFolderInput) -> schema.DeletedFileOutput:
    site, path = _slug(args.site), _path(args.path)
    await ctx.backend.delete_folder(ctx.bearer, site, path, recursive=args.recursive)
    return schema.DeletedFileOutput(site=site, path=path)


async def delete_site(ctx: ToolContext, args: schema.DeleteSiteInput) -> schema.DeletedSiteOutput:
    site = _slug(args.site)
    await ctx.backend.delete_site(ctx.bearer, site, args.confirm)
    return schema.DeletedSiteOutput(slug=site)


# ---------------------------------------------------------------------------
# Specifications


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
            handler=list_sites,
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
            handler=create_site,
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
            handler=list_files,
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
            # Whether a repeat is idempotent depends on `overwrite`, so the hint is left unset.
            annotations=_additive(idempotent=None),
            handler=upload_file,
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
            handler=download_file,
        ),
        ToolSpec(
            name="delete_file",
            title="Delete file",
            description="Delete one file. A folder at the path is refused; use delete_folder.",
            input_model=schema.DeleteFileInput,
            output_model=schema.DeletedFileOutput,
            scope=Scope.REMOVE_DESTRUCTIVE,
            annotations=_destructive(),
            handler=delete_file,
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
            handler=delete_folder,
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
            handler=delete_site,
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


# ---------------------------------------------------------------------------
# Dispatch


def _validation_message(error: ValidationError) -> str:
    return "; ".join(
        f"{'.'.join(str(loc) for loc in err.get('loc', ()))}: {err.get('msg', '')}" for err in error.errors()
    )


async def dispatch(
    specs: tuple[ToolSpec, ...],
    name: str,
    arguments: dict[str, Any],
    ctx: ToolContext,
    scope: Scope,
) -> dict[str, Any]:
    """Run tool `name` for a token of `scope`; return the output model as a JSON-ready dict.

    Refuses, with `ToolError`: an unknown tool (`bad_request`), a tool above the
    token's scope (`forbidden`), arguments that fail the input model
    (`validation_error`), and whatever the handler or the backend refused.
    """
    try:
        spec = spec_for(specs, name)
    except KeyError:
        raise ToolError(
            ErrorCode.BAD_REQUEST, f"no tool named {name!r}; the tools are {', '.join(s.name for s in specs)}"
        ) from None
    if not scope_allows(scope, spec.scope):
        raise ToolError(
            ErrorCode.FORBIDDEN,
            f"the tool {name} needs the {spec.scope.value} scope; the token has {scope.value}",
        )
    try:
        # Strict, so that what the published inputSchema refuses (a string for a boolean) is refused here too.
        args = spec.input_model.model_validate(arguments, strict=True)
    except ValidationError as e:
        raise ToolError(ErrorCode.VALIDATION_ERROR, _validation_message(e)) from e
    try:
        output = await spec.handler(ctx, args)
    except BackendError as e:
        raise ToolError.from_backend(e) from e
    return output.model_dump(mode="json")
