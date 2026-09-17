# SPDX-FileCopyrightText: 2026 Gary Frattarola <garyf@parkviewlab.ai>
#
# SPDX-License-Identifier: MIT OR Apache-2.0

"""The MCP tool specifications: the eight tools of the design, their scopes,
annotations, and input and output schemas that validate real values."""

from __future__ import annotations

from datetime import UTC, datetime

import jsonschema
import pytest

from paper_boxing.common.schema import EntryType, FileEntry, FileListing, Site, UploadResult
from paper_boxing.common.scopes import Scope
from paper_boxing.mcp import schema
from paper_boxing.mcp.tools import TOOL_NAMES, list_tools, spec_for, tool_specs

SPECS = tool_specs(8)

DESIGN_TOOLS = (
    "list_sites",
    "create_site",
    "list_files",
    "upload_file",
    "download_file",
    "delete_file",
    "delete_folder",
    "delete_site",
)


def test_the_design_tools_and_no_others() -> None:
    assert TOOL_NAMES == DESIGN_TOOLS
    assert tuple(s.name for s in SPECS) == DESIGN_TOOLS


def test_scopes() -> None:
    by_scope = {scope: [s.name for s in SPECS if s.scope is scope] for scope in Scope}
    assert by_scope[Scope.READ_ONLY] == ["list_sites", "list_files", "download_file"]
    assert by_scope[Scope.READ_WRITE] == ["create_site", "upload_file"]
    assert by_scope[Scope.REMOVE_DESTRUCTIVE] == ["delete_file", "delete_folder", "delete_site"]


def test_list_tools_filters_by_scope() -> None:
    assert [t.name for t in list_tools(SPECS, Scope.READ_ONLY)] == [
        "list_sites",
        "list_files",
        "download_file",
    ]
    assert len(list_tools(SPECS, Scope.READ_WRITE)) == 5
    assert [t.name for t in list_tools(SPECS, Scope.REMOVE_DESTRUCTIVE)] == list(DESIGN_TOOLS)


def test_spec_for() -> None:
    assert spec_for(SPECS, "delete_site").scope is Scope.REMOVE_DESTRUCTIVE
    with pytest.raises(KeyError):
        spec_for(SPECS, "nowhere")


@pytest.mark.parametrize("spec", SPECS, ids=lambda s: s.name)
def test_tool_has_title_annotations_and_schemas(spec) -> None:
    tool = spec.to_tool()
    assert tool.title == spec.title
    assert tool.description
    assert tool.annotations is not None
    assert tool.inputSchema["type"] == "object"
    assert tool.outputSchema is not None and tool.outputSchema["type"] == "object"
    jsonschema.Draft202012Validator.check_schema(tool.inputSchema)
    jsonschema.Draft202012Validator.check_schema(tool.outputSchema)


@pytest.mark.parametrize("spec", SPECS, ids=lambda s: s.name)
def test_annotations_match_scope(spec) -> None:
    ann = spec.to_tool().annotations
    assert ann is not None
    if spec.scope is Scope.READ_ONLY:
        assert ann.readOnlyHint is True and ann.destructiveHint is False
    elif spec.scope is Scope.READ_WRITE:
        assert ann.readOnlyHint is False and ann.destructiveHint is False
    else:
        assert ann.readOnlyHint is False and ann.destructiveHint is True
    assert ann.openWorldHint is False


def test_large_files_are_pointed_at_the_rest_api() -> None:
    for name in ("upload_file", "download_file"):
        description = spec_for(SPECS, name).description
        assert "8 MiB" in description
        assert "/api/v1/sites/{site}/files/{path}" in description
    assert "16 MiB" in spec_for(tool_specs(16), "upload_file").description


def _example_outputs() -> dict[str, object]:
    now = datetime(2026, 9, 17, tzinfo=UTC)
    site = Site(slug="s", name="S", url="http://h:35841/s/", created_at=now, file_count=1, bytes=3)
    return {
        "list_sites": schema.ListSitesOutput(sites=[site]),
        "create_site": site,
        "list_files": FileListing(
            site="s",
            path="",
            entries=[FileEntry(name="a", type=EntryType.FOLDER, bytes=0, modified_at=now, sha256=None)],
        ),
        "upload_file": UploadResult(path="a/b.txt", bytes=3, sha256="ab" * 32, replaced=False),
        "download_file": schema.DownloadFileOutput(
            site="s",
            path="a/b.txt",
            bytes=3,
            sha256="ab" * 32,
            content_type="text/plain",
            content_base64="YWJj",
        ),
        "delete_file": schema.DeletedFileOutput(site="s", path="a/b.txt"),
        "delete_folder": schema.DeletedFileOutput(site="s", path="a"),
        "delete_site": schema.DeletedSiteOutput(slug="s"),
    }


@pytest.mark.parametrize("spec", SPECS, ids=lambda s: s.name)
def test_example_output_validates_against_output_schema(spec) -> None:
    example = _example_outputs()[spec.name]
    payload = example.model_dump(mode="json")  # type: ignore[attr-defined]
    jsonschema.validate(instance=payload, schema=spec.to_tool().outputSchema)


@pytest.mark.parametrize(
    ("name", "arguments"),
    [
        ("list_sites", {}),
        ("create_site", {"name": "My Site"}),
        ("list_files", {"site": "s"}),
        ("list_files", {"site": "s", "path": "docs"}),
        ("upload_file", {"site": "s", "path": "a.txt", "content_base64": "YWJj"}),
        ("upload_file", {"site": "s", "path": "a.txt", "content_base64": "YWJj", "overwrite": True}),
        ("download_file", {"site": "s", "path": "a.txt"}),
        ("delete_file", {"site": "s", "path": "a.txt"}),
        ("delete_folder", {"site": "s", "path": "docs", "recursive": True}),
        ("delete_site", {"site": "s", "confirm": "s"}),
    ],
)
def test_example_input_validates_against_input_schema(name: str, arguments: dict) -> None:
    jsonschema.validate(instance=arguments, schema=spec_for(SPECS, name).to_tool().inputSchema)


@pytest.mark.parametrize(
    ("name", "arguments"),
    [
        ("create_site", {}),
        ("upload_file", {"site": "s", "path": "a.txt"}),
        ("delete_site", {"site": "s"}),
        ("delete_folder", {"site": "s", "path": "docs", "recursive": "yes"}),
    ],
)
def test_bad_input_fails_input_schema(name: str, arguments: dict) -> None:
    with pytest.raises(jsonschema.ValidationError):
        jsonschema.validate(instance=arguments, schema=spec_for(SPECS, name).to_tool().inputSchema)
