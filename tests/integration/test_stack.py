# SPDX-FileCopyrightText: 2026 Gary Frattarola <garyf@parkviewlab.ai>
#
# SPDX-License-Identifier: MIT OR Apache-2.0

"""The compose stack: /health on the three services, the MCP handshake over the
published port, and nginx serving a site from the data volume.

The site files are placed in the volume through the backend container, since
the scaffold has no upload route yet; the integration PR replaces that with
uploads through the API and through MCP.
"""

from __future__ import annotations

import subprocess
from collections.abc import Iterator

import httpx
import pytest

from tests.integration.conftest import BACKEND, FRONTEND, MCP, SITES

pytestmark = pytest.mark.integration

BACKEND_CONTAINER = "paper-boxing-backend"


def _exec(script: str) -> None:
    subprocess.run(["docker", "exec", BACKEND_CONTAINER, "sh", "-c", script], check=True, capture_output=True)


@pytest.mark.parametrize("base", [BACKEND, FRONTEND, MCP], ids=["backend", "frontend", "mcp"])
def test_health(base: str) -> None:
    body = httpx.get(f"{base}/health", timeout=5.0).json()
    assert body["ok"] is True
    assert body["version"]
    assert body["uptime_seconds"] >= 0


def test_one_version_for_the_stack() -> None:
    versions = {
        httpx.get(f"{base}/health", timeout=5.0).json()["version"] for base in (BACKEND, FRONTEND, MCP)
    }
    assert len(versions) == 1
    names = {
        httpx.get(f"{base}/admin/version", timeout=5.0).json()["name"] for base in (BACKEND, FRONTEND, MCP)
    }
    assert names == {"paper-boxing-backend", "paper-boxing-frontend", "paper-boxing-mcp"}


def test_mcp_initialize_over_the_published_port() -> None:
    payload = {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "initialize",
        "params": {
            "protocolVersion": "2025-06-18",
            "capabilities": {},
            "clientInfo": {"name": "it", "version": "0"},
        },
    }
    resp = httpx.post(
        f"{MCP}/mcp",
        json=payload,
        headers={"Accept": "application/json, text/event-stream", "Content-Type": "application/json"},
        timeout=10.0,
    )
    assert resp.status_code == 200, resp.text
    assert "paper-boxing-mcp" in resp.text
    assert httpx.get(f"{MCP}/mcp", timeout=5.0).status_code == 405
    foreign = httpx.post(f"{MCP}/mcp", json=payload, headers={"Host": "evil.example.com"}, timeout=5.0)
    assert foreign.status_code == 421


@pytest.fixture(scope="module")
def served_sites() -> Iterator[None]:
    _exec(
        "set -e; mkdir -p /data/sites/with-index/css /data/sites/no-index;"
        " printf '<!doctype html><title>with-index</title><p>served</p>' > /data/sites/with-index/index.html;"
        " printf 'p{color:red}' > /data/sites/with-index/css/a.css;"
        " printf 'export const a=1;' > /data/sites/with-index/m.mjs;"
        " printf '{}' > /data/sites/with-index/app.webmanifest;"
        " printf 'hello' > /data/sites/no-index/a.txt"
    )
    try:
        yield
    finally:
        _exec("rm -rf /data/sites/with-index /data/sites/no-index")


def test_nginx_serves_index_and_assets(served_sites: None) -> None:
    page = httpx.get(f"{SITES}/with-index/", timeout=5.0)
    assert page.status_code == 200
    assert page.headers["content-type"].startswith("text/html")
    assert "served" in page.text
    assert page.headers["cache-control"] == "no-cache"
    assert "etag" in page.headers
    css = httpx.get(f"{SITES}/with-index/css/a.css", timeout=5.0)
    assert css.status_code == 200 and css.headers["content-type"].startswith("text/css")
    mjs = httpx.get(f"{SITES}/with-index/m.mjs", timeout=5.0)
    assert mjs.status_code == 200 and mjs.headers["content-type"].startswith("application/javascript")
    manifest = httpx.get(f"{SITES}/with-index/app.webmanifest", timeout=5.0)
    assert manifest.status_code == 200 and manifest.headers["content-type"].startswith(
        "application/manifest+json"
    )


def test_nginx_redirect_keeps_the_published_port(served_sites: None) -> None:
    resp = httpx.get(f"{SITES}/with-index", timeout=5.0)
    assert resp.status_code == 301
    assert resp.headers["location"] == "/with-index/"  # absolute_redirect off: no host, no port 80


def test_nginx_lists_a_folder_without_index(served_sites: None) -> None:
    listing = httpx.get(f"{SITES}/no-index/", timeout=5.0)
    assert listing.status_code == 200
    assert "a.txt" in listing.text
    assert httpx.get(f"{SITES}/no-index/a.txt", timeout=5.0).text == "hello"


def test_nginx_missing_path_is_404(served_sites: None) -> None:
    assert httpx.get(f"{SITES}/nowhere/", timeout=5.0).status_code == 404
    assert httpx.get(f"{SITES}/with-index/nowhere.html", timeout=5.0).status_code == 404


def test_nginx_does_not_expose_the_rest_of_the_volume(served_sites: None) -> None:
    assert httpx.get(f"{SITES}/../paper-boxing.sqlite3", timeout=5.0).status_code in (400, 404)
    assert httpx.get(f"{SITES}/%2e%2e/staging/", timeout=5.0).status_code in (400, 404)
