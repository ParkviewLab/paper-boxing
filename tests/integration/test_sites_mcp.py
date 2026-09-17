# SPDX-FileCopyrightText: 2026 Gary Frattarola <garyf@parkviewlab.ai>
#
# SPDX-License-Identifier: MIT OR Apache-2.0

"""A second site made through the MCP server with an agent token, as an agent
makes one: the SDK's Streamable-HTTP client over the published port, the tools
listed by scope, a site created and a tree uploaded with `upload_file` in one
session, every file read back with `download_file` and fetched through nginx
byte for byte with its type, the index-less root listed by nginx, and the
deletions through the tools, each confirmed through nginx and, for the site,
on the volume. The intent guards hold across the stack: a non-empty folder
needs `recursive`, a site needs its slug as `confirm`, and a read-only token
sees three tools and is refused a write.

The tests run in file order: the deletions come last, and the site fixture's
teardown removes the site through the admin's session if a test has not.
"""

from __future__ import annotations

import base64
import secrets
from collections.abc import Iterator

import pytest
from mcp import ClientSession

from tests.integration._mcp_client import Agent, ToolRefused, call
from tests.integration._tree import site_tree
from tests.integration.conftest import (
    MCP,
    PUBLIC_SITES_URL,
    AgentToken,
    Deployed,
    Rest,
    assert_nothing_remains_on_the_volume,
    assert_served_as_uploaded,
    fetch,
    media_type,
)

pytestmark = pytest.mark.integration

READ_ONLY_TOOLS = {"list_sites", "list_files", "download_file"}
ALL_TOOLS = READ_ONLY_TOOLS | {"create_site", "upload_file", "delete_file", "delete_folder", "delete_site"}


@pytest.fixture(scope="module")
def agent(agent_tokens: dict[str, AgentToken]) -> Agent:
    return Agent(url=f"{MCP}/mcp", token=agent_tokens["remove_destructive"].secret)


@pytest.fixture(scope="module")
def reader(agent_tokens: dict[str, AgentToken]) -> Agent:
    return Agent(url=f"{MCP}/mcp", token=agent_tokens["read_only"].secret)


@pytest.fixture(scope="module")
def mcp_site(agent: Agent, admin: Rest) -> Iterator[Deployed]:
    """A site without an `index.html`, created and filled through the tools in one MCP session."""
    suffix = secrets.token_hex(3)
    name = f"Integration MCP {suffix}"
    tree = site_tree(name, with_index=False)

    async def create_and_fill(session: ClientSession) -> dict[str, object]:
        site = await call(session, "create_site", {"name": name})
        for file in tree:
            result = await call(
                session,
                "upload_file",
                {
                    "site": site["slug"],
                    "path": file.path,
                    "content_base64": base64.b64encode(file.data).decode("ascii"),
                },
            )
            assert result == {
                "path": file.path,
                "bytes": len(file.data),
                "sha256": file.sha256,
                "replaced": False,
            }
        return site

    site = agent.run(create_and_fill)
    slug = str(site["slug"])
    assert slug == f"integration-mcp-{suffix}"
    assert site["url"] == f"{PUBLIC_SITES_URL}/{slug}/"
    try:
        yield Deployed(slug=slug, tree=tree)
    finally:
        admin.delete_site_if_present(slug)


def test_tools_are_listed_and_refused_by_scope(agent: Agent, reader: Agent) -> None:
    assert set(reader.tool_names()) == READ_ONLY_TOOLS
    assert set(agent.tool_names()) == ALL_TOOLS
    with pytest.raises(ToolRefused) as refused:
        reader.call("create_site", {"name": "Integration refused"})
    assert refused.value.code == "forbidden"
    assert "read_write" in refused.value.message


def test_the_agent_sees_its_site_and_files(agent: Agent, mcp_site: Deployed) -> None:
    sites = {site["slug"]: site for site in agent.call("list_sites", {})["sites"]}
    site = sites[mcp_site.slug]
    assert site["file_count"] == len(mcp_site.tree)
    assert site["bytes"] == sum(len(file.data) for file in mcp_site.tree)
    root = agent.call("list_files", {"site": mcp_site.slug})
    assert root["path"] == ""
    assert [entry["name"] for entry in root["entries"] if entry["type"] == "folder"] == [
        "css",
        "docs",
        "fonts",
        "img",
        "js",
        "notes",
    ]
    assert [entry["name"] for entry in root["entries"] if entry["type"] == "file"] == ["app.webmanifest"]
    guide = agent.call("list_files", {"site": mcp_site.slug, "path": "docs/guide"})
    assert guide["entries"][0]["sha256"] == mcp_site.file("docs/guide/chapter-1.html").sha256


def test_download_file_round_trips_every_file(agent: Agent, mcp_site: Deployed) -> None:
    async def download_all(session: ClientSession) -> None:
        for file in mcp_site.tree:
            result = await call(session, "download_file", {"site": mcp_site.slug, "path": file.path})
            assert base64.b64decode(result["content_base64"]) == file.data, file.path
            assert result["sha256"] == file.sha256
            assert result["bytes"] == len(file.data)
            assert result["content_type"]

    agent.run(download_all)


def test_every_file_is_served_byte_identical_with_its_type(mcp_site: Deployed) -> None:
    for file in mcp_site.tree:
        assert_served_as_uploaded(mcp_site.slug, file)


def test_the_index_less_root_is_listed(mcp_site: Deployed) -> None:
    root = fetch(mcp_site.slug)
    assert root.status_code == 200
    assert media_type(root) == "text/html"
    for name in ("css/", "docs/", "fonts/", "img/", "js/", "notes/", "app.webmanifest"):
        assert f'href="{name}"' in root.text, name
    assert fetch(mcp_site.slug, "index.html").status_code == 404


def test_deleting_a_file_through_the_tool_stops_it_being_served(agent: Agent, mcp_site: Deployed) -> None:
    assert fetch(mcp_site.slug, "js/app.js").status_code == 200
    assert agent.call("delete_file", {"site": mcp_site.slug, "path": "js/app.js"}) == {
        "site": mcp_site.slug,
        "path": "js/app.js",
        "deleted": True,
    }
    assert fetch(mcp_site.slug, "js/app.js").status_code == 404
    assert fetch(mcp_site.slug, "js/module.mjs").status_code == 200


def test_deleting_a_folder_through_the_tool_needs_recursive(agent: Agent, mcp_site: Deployed) -> None:
    with pytest.raises(ToolRefused) as refused:
        agent.call("delete_folder", {"site": mcp_site.slug, "path": "docs"})
    assert refused.value.code == "folder_not_empty"
    assert fetch(mcp_site.slug, "docs/design.html").status_code == 200
    assert agent.call("delete_folder", {"site": mcp_site.slug, "path": "docs", "recursive": True}) == {
        "site": mcp_site.slug,
        "path": "docs",
        "deleted": True,
    }
    for path in ("docs/design.html", "docs/guide/chapter-1.html", "docs/guide/", "docs/"):
        assert fetch(mcp_site.slug, path).status_code == 404, path


def test_deleting_the_site_through_the_tool_removes_it_everywhere(agent: Agent, mcp_site: Deployed) -> None:
    with pytest.raises(ToolRefused) as refused:
        agent.call("delete_site", {"site": mcp_site.slug, "confirm": "wrong"})
    assert refused.value.code == "confirm_mismatch"
    assert fetch(mcp_site.slug).status_code == 200
    assert agent.call("delete_site", {"site": mcp_site.slug, "confirm": mcp_site.slug}) == {
        "slug": mcp_site.slug,
        "deleted": True,
    }
    assert fetch(mcp_site.slug).status_code == 404
    assert fetch(mcp_site.slug, "css/site.css").status_code == 404
    assert mcp_site.slug not in {site["slug"] for site in agent.call("list_sites", {})["sites"]}
    assert_nothing_remains_on_the_volume(mcp_site.slug)
