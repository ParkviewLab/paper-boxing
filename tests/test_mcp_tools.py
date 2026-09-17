# SPDX-FileCopyrightText: 2026 Gary Frattarola <garyf@parkviewlab.ai>
#
# SPDX-License-Identifier: MIT OR Apache-2.0

"""The eight tools through /mcp against the fake backend: listed and refused
by scope, round trips of a text file and a PNG compared by sha256, every
tool's output validated against its outputSchema, the file-size cap, and the
backend's refusals coming back as tool errors in the contract's shape."""

from __future__ import annotations

import base64
import hashlib
import json
import struct
import uuid
import zlib
from typing import Any

import jsonschema
import pytest
from fastapi.testclient import TestClient

from paper_boxing.common.fake_backend import FakeFile, FakeState
from paper_boxing.common.scopes import Scope
from paper_boxing.mcp.tools import TOOL_NAMES
from tests._mcp_helpers import call_tool, list_tools

CAP = 8 * 1024 * 1024  # PAPER_BOXING_MCP_MAX_FILE_MB's default, decoded

BY_SCOPE = {
    Scope.READ_ONLY: ["list_sites", "list_files", "download_file"],
    Scope.READ_WRITE: ["list_sites", "create_site", "list_files", "upload_file", "download_file"],
    Scope.REMOVE_DESTRUCTIVE: list(TOOL_NAMES),
}


def _agent(fake_state: FakeState, scope: Scope) -> str:
    return fake_state.issue_agent_token("admin", f"{scope.value}-{uuid.uuid4().hex[:6]}", scope)[1]


def _site(client: TestClient, token: str) -> str:
    """A fresh site under a name no other test uses; returns its slug."""
    result = call_tool(client, "create_site", {"name": f"Site {uuid.uuid4().hex[:10]}"}, token=token)
    return _ok(result)["slug"]


def _b64(content: bytes) -> str:
    return base64.b64encode(content).decode("ascii")


def _ok(result: dict[str, Any]) -> dict[str, Any]:
    assert result["isError"] is False, result
    return result["structuredContent"]


def _error(result: dict[str, Any]) -> dict[str, str]:
    """The `{code, message}` of a tool error, read from its text content."""
    assert result["isError"] is True, result
    assert "structuredContent" not in result or result["structuredContent"] is None
    return json.loads(result["content"][0]["text"])["error"]


def _png() -> bytes:
    """A valid 2x2 RGBA PNG, built in memory."""

    def chunk(kind: bytes, data: bytes) -> bytes:
        return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data))

    rows = b"".join(b"\x00" + bytes(pixel) * 2 for pixel in ((255, 0, 0, 255), (0, 0, 255, 255)))
    ihdr = struct.pack(">IIBBBBB", 2, 2, 8, 6, 0, 0, 0)
    return (
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", ihdr)
        + chunk(b"IDAT", zlib.compress(rows))
        + chunk(b"IEND", b"")
    )


# ---------------------------------------------------------------------------
# Listed and refused by scope


@pytest.mark.parametrize("scope", list(Scope), ids=lambda s: s.value)
def test_tools_listed_per_scope(mcp_client: TestClient, fake_state: FakeState, scope: Scope) -> None:
    tools = list_tools(mcp_client, token=_agent(fake_state, scope))
    assert [t["name"] for t in tools] == BY_SCOPE[scope]
    for tool in tools:
        assert tool["outputSchema"]["type"] == "object"
        assert tool["annotations"] is not None


ABOVE_SCOPE = [
    ("create_site", Scope.READ_ONLY, {"name": "Nope"}),
    ("upload_file", Scope.READ_ONLY, {"site": "s", "path": "a.txt", "content_base64": "YWJj"}),
    ("delete_file", Scope.READ_ONLY, {"site": "s", "path": "a.txt"}),
    ("delete_folder", Scope.READ_ONLY, {"site": "s", "path": "docs"}),
    ("delete_site", Scope.READ_ONLY, {"site": "s", "confirm": "s"}),
    ("delete_file", Scope.READ_WRITE, {"site": "s", "path": "a.txt"}),
    ("delete_folder", Scope.READ_WRITE, {"site": "s", "path": "docs"}),
    ("delete_site", Scope.READ_WRITE, {"site": "s", "confirm": "s"}),
]


@pytest.mark.parametrize(("name", "scope", "arguments"), ABOVE_SCOPE, ids=lambda v: getattr(v, "value", v))
def test_call_above_scope_is_refused_as_a_tool_error(
    mcp_client: TestClient, fake_state: FakeState, name: str, scope: Scope, arguments: dict[str, Any]
) -> None:
    start = len(fake_state.requests)
    error = _error(call_tool(mcp_client, name, arguments, token=_agent(fake_state, scope)))
    assert error["code"] == "forbidden"
    assert name in error["message"] and scope.value in error["message"]
    assert [r.route for r in fake_state.requests[start:]] == ["token_self"], "refused before the backend"


# ---------------------------------------------------------------------------
# Round trips


def test_text_file_round_trip_by_sha256(mcp_client: TestClient, fake_state: FakeState) -> None:
    rw = _agent(fake_state, Scope.READ_WRITE)
    ro = _agent(fake_state, Scope.READ_ONLY)
    slug = _site(mcp_client, rw)
    content = "Notes: café, naïve, 日本語 — line two\n".encode()
    digest = hashlib.sha256(content).hexdigest()

    up = _ok(
        call_tool(
            mcp_client,
            "upload_file",
            {"site": slug, "path": "docs/notes.txt", "content_base64": _b64(content)},
            token=rw,
        )
    )
    assert up == {"path": "docs/notes.txt", "bytes": len(content), "sha256": digest, "replaced": False}

    down = _ok(call_tool(mcp_client, "download_file", {"site": slug, "path": "docs/notes.txt"}, token=ro))
    assert base64.b64decode(down["content_base64"]) == content
    assert down["sha256"] == digest and down["bytes"] == len(content)
    assert down["content_type"].startswith("text/plain")
    assert (down["site"], down["path"]) == (slug, "docs/notes.txt")

    listing = _ok(call_tool(mcp_client, "list_files", {"site": slug, "path": "docs"}, token=ro))
    assert listing["path"] == "docs"
    assert [(e["name"], e["type"], e["sha256"]) for e in listing["entries"]] == [
        ("notes.txt", "file", digest)
    ]
    root = _ok(call_tool(mcp_client, "list_files", {"site": slug}, token=ro))
    assert [(e["name"], e["type"], e["bytes"], e["sha256"]) for e in root["entries"]] == [
        ("docs", "folder", len(content), None)
    ]


def test_png_round_trip_by_sha256(mcp_client: TestClient, fake_state: FakeState) -> None:
    rw = _agent(fake_state, Scope.READ_WRITE)
    slug = _site(mcp_client, rw)
    png = _png()
    digest = hashlib.sha256(png).hexdigest()

    up = _ok(
        call_tool(
            mcp_client,
            "upload_file",
            {"site": slug, "path": "img/pixel.png", "content_base64": _b64(png)},
            token=rw,
        )
    )
    assert up["sha256"] == digest and up["bytes"] == len(png)
    down = _ok(call_tool(mcp_client, "download_file", {"site": slug, "path": "img/pixel.png"}, token=rw))
    assert base64.b64decode(down["content_base64"]) == png
    assert hashlib.sha256(base64.b64decode(down["content_base64"])).hexdigest() == down["sha256"] == digest
    assert down["content_type"] == "image/png"


def test_base64_with_line_breaks_is_accepted(mcp_client: TestClient, fake_state: FakeState) -> None:
    rw = _agent(fake_state, Scope.READ_WRITE)
    slug = _site(mcp_client, rw)
    content = bytes(range(256)) * 4
    wrapped = base64.encodebytes(content).decode("ascii")  # 76-column lines with newlines
    assert "\n" in wrapped
    up = _ok(
        call_tool(
            mcp_client, "upload_file", {"site": slug, "path": "bin.dat", "content_base64": wrapped}, token=rw
        )
    )
    assert up["sha256"] == hashlib.sha256(content).hexdigest()


# ---------------------------------------------------------------------------
# Every tool's output against its outputSchema


def test_every_tool_output_validates_against_its_output_schema(
    mcp_client: TestClient, fake_state: FakeState
) -> None:
    rd = _agent(fake_state, Scope.REMOVE_DESTRUCTIVE)
    schemas = {t["name"]: t["outputSchema"] for t in list_tools(mcp_client, token=rd)}
    assert set(schemas) == set(TOOL_NAMES)
    name = f"Schema {uuid.uuid4().hex[:8]}"
    content = b"<!doctype html><title>x</title>"

    def run(tool: str, arguments: dict[str, Any]) -> dict[str, Any]:
        result = call_tool(mcp_client, tool, arguments, token=rd)
        structured = _ok(result)
        jsonschema.validate(instance=structured, schema=schemas[tool])
        assert json.loads(result["content"][0]["text"]) == structured, (
            "the text content is the structured one"
        )
        return structured

    slug = run("create_site", {"name": name})["slug"]
    run("upload_file", {"site": slug, "path": "docs/index.html", "content_base64": _b64(content)})
    run("list_files", {"site": slug})
    run("list_files", {"site": slug, "path": "docs"})
    run("download_file", {"site": slug, "path": "docs/index.html"})
    sites = run("list_sites", {})
    assert slug in {s["slug"] for s in sites["sites"]}
    assert run("delete_file", {"site": slug, "path": "docs/index.html"}) == {
        "site": slug,
        "path": "docs/index.html",
        "deleted": True,
    }
    assert run("delete_folder", {"site": slug, "path": "docs"}) == {
        "site": slug,
        "path": "docs",
        "deleted": True,
    }
    assert run("delete_site", {"site": slug, "confirm": slug}) == {"slug": slug, "deleted": True}
    assert slug not in fake_state.sites


# ---------------------------------------------------------------------------
# The size cap


def test_upload_above_the_cap_is_refused_naming_the_rest_route(
    mcp_client: TestClient, fake_state: FakeState
) -> None:
    rw = _agent(fake_state, Scope.READ_WRITE)
    slug = _site(mcp_client, rw)
    start = len(fake_state.requests)
    error = _error(
        call_tool(
            mcp_client,
            "upload_file",
            {"site": slug, "path": "big/blob.bin", "content_base64": _b64(b"\0" * (CAP + 1))},
            token=rw,
        )
    )
    assert error["code"] == "payload_too_large"
    assert f"PUT /api/v1/sites/{slug}/files/big/blob.bin" in error["message"]
    assert [r.route for r in fake_state.requests[start:]] == ["token_self"], "nothing was sent to the backend"
    assert "big/blob.bin" not in fake_state.sites[slug].files


def test_upload_at_the_cap_passes_the_transport(mcp_client: TestClient, fake_state: FakeState) -> None:
    """The transport's request-body limit fits a file at the cap, base64-encoded (the SDK's default would not)."""
    rw = _agent(fake_state, Scope.READ_WRITE)
    slug = _site(mcp_client, rw)
    content = b"\xa5" * CAP
    up = _ok(
        call_tool(
            mcp_client,
            "upload_file",
            {"site": slug, "path": "at-cap.bin", "content_base64": _b64(content)},
            token=rw,
        )
    )
    assert up["bytes"] == CAP and up["sha256"] == hashlib.sha256(content).hexdigest()


def test_download_above_the_cap_is_refused_naming_the_rest_route(
    mcp_client: TestClient, fake_state: FakeState
) -> None:
    rw = _agent(fake_state, Scope.READ_WRITE)
    slug = _site(mcp_client, rw)
    fake_state.sites[slug].files["huge.bin"] = FakeFile(
        content=b"\0" * (CAP + 1), modified_at=fake_state.clock.now()
    )
    error = _error(call_tool(mcp_client, "download_file", {"site": slug, "path": "huge.bin"}, token=rw))
    assert error["code"] == "payload_too_large"
    assert f"GET /api/v1/sites/{slug}/files/huge.bin" in error["message"]


def test_descriptions_name_the_cap_and_the_rest_route(mcp_client: TestClient, fake_state: FakeState) -> None:
    tools = {t["name"]: t for t in list_tools(mcp_client, token=_agent(fake_state, Scope.READ_ONLY))}
    assert "8 MiB" in tools["download_file"]["description"]
    assert "/api/v1/sites/{site}/files/{path}" in tools["download_file"]["description"]


# ---------------------------------------------------------------------------
# The backend's refusals, and the handlers' own, as tool errors


def test_backend_refusals_become_tool_errors_with_their_code(
    mcp_client: TestClient, fake_state: FakeState
) -> None:
    rd = _agent(fake_state, Scope.REMOVE_DESTRUCTIVE)
    name = f"Dup {uuid.uuid4().hex[:8]}"
    slug = _ok(call_tool(mcp_client, "create_site", {"name": name}, token=rd))["slug"]
    _ok(
        call_tool(
            mcp_client, "upload_file", {"site": slug, "path": "a/b.txt", "content_base64": "YWJj"}, token=rd
        )
    )

    def err(tool: str, arguments: dict[str, Any]) -> str:
        return _error(call_tool(mcp_client, tool, arguments, token=rd))["code"]

    assert err("create_site", {"name": name}) == "site_exists"
    assert err("create_site", {"name": "!!!"}) == "invalid_name"
    assert err("upload_file", {"site": slug, "path": "a/b.txt", "content_base64": "eA=="}) == "file_exists"
    assert err("upload_file", {"site": slug, "path": "a", "content_base64": "eA=="}) == "not_a_file"
    assert err("upload_file", {"site": slug, "path": "a/b.txt/c", "content_base64": "eA=="}) == "not_a_folder"
    assert err("list_files", {"site": "no-such-site"}) == "not_found"
    assert err("list_files", {"site": slug, "path": "a/b.txt"}) == "not_a_folder"
    assert err("download_file", {"site": slug, "path": "a"}) == "not_a_file"
    assert err("download_file", {"site": slug, "path": "a/missing.txt"}) == "not_found"
    assert err("delete_file", {"site": slug, "path": "a"}) == "not_a_file"
    assert err("delete_folder", {"site": slug, "path": "a/b.txt"}) == "not_a_folder"
    assert err("delete_folder", {"site": slug, "path": "a"}) == "folder_not_empty"
    assert err("delete_site", {"site": slug, "confirm": "wrong"}) == "confirm_mismatch"
    assert err("delete_site", {"site": "no-such-site", "confirm": "no-such-site"}) == "not_found"
    assert slug in fake_state.sites, "nothing above deleted anything"


def test_handler_refusals_before_the_backend(mcp_client: TestClient, fake_state: FakeState) -> None:
    rd = _agent(fake_state, Scope.REMOVE_DESTRUCTIVE)
    start = len(fake_state.requests)

    def refusal(tool: str, arguments: dict[str, Any]) -> dict[str, str]:
        return _error(call_tool(mcp_client, tool, arguments, token=rd))

    assert refusal("list_files", {"site": "s", "path": "../etc"})["code"] == "invalid_path"
    assert (
        refusal("upload_file", {"site": "s", "path": "", "content_base64": "eA=="})["code"] == "invalid_path"
    )
    assert refusal("delete_folder", {"site": "s", "path": "/"})["code"] == "invalid_path"
    assert refusal("upload_file", {"site": "s", "path": "x.bin", "content_base64": "not base64!"})[
        "code"
    ] == ("validation_error")
    assert refusal("delete_site", {"site": "s"})["code"] == "validation_error"
    assert (
        refusal("delete_folder", {"site": "s", "path": "d", "recursive": "yes"})["code"] == "validation_error"
    )
    assert refusal("list_files", {"site": "Not A Slug"})["code"] == "not_found"
    unknown = refusal("archive_site", {"site": "s"})
    assert unknown["code"] == "bad_request" and "archive_site" in unknown["message"]
    assert [r.route for r in fake_state.requests[start:]] == ["token_self"] * 8, "none reached the backend"


# ---------------------------------------------------------------------------
# Intent before destruction


def test_overwrite_is_explicit_and_reported(mcp_client: TestClient, fake_state: FakeState) -> None:
    rw = _agent(fake_state, Scope.READ_WRITE)
    slug = _site(mcp_client, rw)
    args = {"site": slug, "path": "index.html", "content_base64": _b64(b"one")}
    assert _ok(call_tool(mcp_client, "upload_file", args, token=rw))["replaced"] is False
    assert _error(call_tool(mcp_client, "upload_file", args, token=rw))["code"] == "file_exists"
    again = _ok(
        call_tool(
            mcp_client, "upload_file", {**args, "content_base64": _b64(b"two"), "overwrite": True}, token=rw
        )
    )
    assert again["replaced"] is True and again["sha256"] == hashlib.sha256(b"two").hexdigest()
    assert fake_state.sites[slug].files["index.html"].content == b"two"


def test_delete_folder_needs_recursive_for_a_non_empty_one(
    mcp_client: TestClient, fake_state: FakeState
) -> None:
    rd = _agent(fake_state, Scope.REMOVE_DESTRUCTIVE)
    slug = _site(mcp_client, rd)
    _ok(
        call_tool(
            mcp_client, "upload_file", {"site": slug, "path": "d/e/f.txt", "content_base64": "eA=="}, token=rd
        )
    )
    assert _error(call_tool(mcp_client, "delete_folder", {"site": slug, "path": "d"}, token=rd))["code"] == (
        "folder_not_empty"
    )
    assert _ok(
        call_tool(mcp_client, "delete_folder", {"site": slug, "path": "d", "recursive": True}, token=rd)
    ) == {
        "site": slug,
        "path": "d",
        "deleted": True,
    }
    assert _ok(call_tool(mcp_client, "list_files", {"site": slug}, token=rd))["entries"] == []


def test_delete_site_needs_its_slug_as_confirm(mcp_client: TestClient, fake_state: FakeState) -> None:
    rd = _agent(fake_state, Scope.REMOVE_DESTRUCTIVE)
    slug = _site(mcp_client, rd)
    assert _error(call_tool(mcp_client, "delete_site", {"site": slug, "confirm": slug[:-1]}, token=rd))[
        "code"
    ] == ("confirm_mismatch")
    assert slug in fake_state.sites
    assert _ok(call_tool(mcp_client, "delete_site", {"site": slug, "confirm": slug}, token=rd)) == {
        "slug": slug,
        "deleted": True,
    }
    assert slug not in fake_state.sites
