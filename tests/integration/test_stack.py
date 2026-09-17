# SPDX-FileCopyrightText: 2026 Gary Frattarola <garyf@parkviewlab.ai>
#
# SPDX-License-Identifier: MIT OR Apache-2.0

"""The compose stack over its published ports: `/health` and one version on
the three services, the frontend's sign-in gate, the MCP endpoint's handshake
with a token minted through the real backend, its refusals (no token, a
session token, a foreign Host, a foreign Origin, the wrong verb, the legacy
path), and nginx keeping the rest of the data volume out of reach.
"""

from __future__ import annotations

import httpx
import pytest

from tests.integration.conftest import (
    BACKEND,
    FRONTEND,
    MCP,
    MCP_HEADERS,
    MCP_INIT,
    SITES,
    TIMEOUT,
    AgentToken,
    Rest,
)

pytestmark = pytest.mark.integration


@pytest.mark.parametrize("base", [BACKEND, FRONTEND, MCP], ids=["backend", "frontend", "mcp"])
def test_health(base: str) -> None:
    body = httpx.get(f"{base}/health", timeout=TIMEOUT).json()
    assert body["ok"] is True
    assert body["version"]
    assert body["uptime_seconds"] >= 0


def test_one_version_for_the_stack() -> None:
    versions = {
        httpx.get(f"{base}/health", timeout=TIMEOUT).json()["version"] for base in (BACKEND, FRONTEND, MCP)
    }
    assert len(versions) == 1
    names = {
        httpx.get(f"{base}/admin/version", timeout=TIMEOUT).json()["name"]
        for base in (BACKEND, FRONTEND, MCP)
    }
    assert names == {"paper-boxing-backend", "paper-boxing-frontend", "paper-boxing-mcp"}


def test_frontend_sends_a_visitor_to_sign_in() -> None:
    """A page without a session goes to /login and comes back afterwards; a download gets the API's 401."""
    home = httpx.get(f"{FRONTEND}/", timeout=TIMEOUT, follow_redirects=False)
    assert home.status_code == 303
    assert home.headers["location"] == "/login"
    site_page = httpx.get(f"{FRONTEND}/sites/some-site?path=docs", timeout=TIMEOUT, follow_redirects=False)
    assert site_page.status_code == 303
    assert site_page.headers["location"] == "/login?next=%2Fsites%2Fsome-site%3Fpath%3Ddocs"
    download = httpx.get(f"{FRONTEND}/download/some-site/index.html", timeout=TIMEOUT, follow_redirects=False)
    assert download.status_code == 401
    assert download.json()["error"]["code"] == "unauthorized"
    login = httpx.get(f"{FRONTEND}/login", timeout=TIMEOUT)
    assert login.status_code == 200
    assert login.headers["content-type"].startswith("text/html")


def test_mcp_handshake_over_the_published_port(admin: Rest, agent_tokens: dict[str, AgentToken]) -> None:
    """Every request to /mcp needs an agent token, the handshake included (docs/design.md, section 4a)."""
    anonymous = httpx.post(f"{MCP}/mcp", json=MCP_INIT, headers=MCP_HEADERS, timeout=TIMEOUT)
    assert anonymous.status_code == 401, anonymous.text
    assert anonymous.json()["error"]["code"] == "unauthorized"
    assert anonymous.headers["www-authenticate"].startswith("Bearer")

    agent = {**MCP_HEADERS, "Authorization": f"Bearer {agent_tokens['read_only'].secret}"}
    handshake = httpx.post(f"{MCP}/mcp", json=MCP_INIT, headers=agent, timeout=TIMEOUT)
    assert handshake.status_code == 200, handshake.text
    assert "paper-boxing-mcp" in handshake.text

    session = {**MCP_HEADERS, "Authorization": f"Bearer {admin.token}"}
    refused = httpx.post(f"{MCP}/mcp", json=MCP_INIT, headers=session, timeout=TIMEOUT)
    assert refused.status_code == 403, refused.text
    assert refused.json()["error"]["code"] == "wrong_token_type"


def test_mcp_transport_rules_over_the_published_port(agent_tokens: dict[str, AgentToken]) -> None:
    """GET is 405; a foreign Host is 421 and a foreign browser Origin 403, before any token is confirmed;
    the legacy /sse path answers 405 naming /mcp."""
    agent = {**MCP_HEADERS, "Authorization": f"Bearer {agent_tokens['read_only'].secret}"}
    verb = httpx.get(f"{MCP}/mcp", timeout=TIMEOUT)
    assert verb.status_code == 405
    assert verb.json()["error"]["code"] == "method_not_allowed"

    foreign_host = httpx.post(
        f"{MCP}/mcp", json=MCP_INIT, headers={**agent, "Host": "evil.example.com"}, timeout=TIMEOUT
    )
    assert foreign_host.status_code == 421, foreign_host.text
    assert foreign_host.json()["error"]["code"] == "forbidden"
    assert "PAPER_BOXING_MCP_ALLOWED_HOSTS" in foreign_host.json()["error"]["message"]

    foreign_origin = httpx.post(
        f"{MCP}/mcp", json=MCP_INIT, headers={**agent, "Origin": "https://evil.example.com"}, timeout=TIMEOUT
    )
    assert foreign_origin.status_code == 403, foreign_origin.text
    assert foreign_origin.json()["error"]["code"] == "forbidden"
    assert "PAPER_BOXING_MCP_ALLOWED_ORIGINS" in foreign_origin.json()["error"]["message"]

    legacy = httpx.post(f"{MCP}/sse", json=MCP_INIT, headers=agent, timeout=TIMEOUT)
    assert legacy.status_code == 405
    assert "/mcp" in legacy.json()["error"]["message"]


def test_nginx_does_not_expose_the_rest_of_the_volume() -> None:
    """`root` is the sites/ tree: the database and the staging area beside it are not reachable."""
    assert httpx.get(f"{SITES}/../paper-boxing.sqlite3", timeout=TIMEOUT).status_code in (400, 404)
    assert httpx.get(f"{SITES}/%2e%2e/staging/", timeout=TIMEOUT).status_code in (400, 404)
    assert httpx.get(f"{SITES}/paper-boxing.sqlite3", timeout=TIMEOUT).status_code == 404
    assert httpx.get(f"{SITES}/staging/", timeout=TIMEOUT).status_code == 404
