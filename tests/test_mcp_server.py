# SPDX-FileCopyrightText: 2026 Gary Frattarola <garyf@parkviewlab.ai>
#
# SPDX-License-Identifier: MIT OR Apache-2.0

"""The MCP server's transport plumbing: the ops endpoints, the /mcp verbs, the
legacy /sse path, and a successful initialize handshake through /mcp."""

from __future__ import annotations

from fastapi.testclient import TestClient

from paper_boxing.common.config import VERSION
from paper_boxing.common.schema import Health
from tests._mcp_helpers import initialize


def test_health(mcp_client: TestClient) -> None:
    health = Health.model_validate(mcp_client.get("/health").json())
    assert health.ok is True
    assert health.version == VERSION


def test_admin_version(mcp_client: TestClient) -> None:
    body = mcp_client.get("/admin/version").json()
    assert body["name"] == "paper-boxing-mcp"
    assert body["version"] == VERSION
    assert body["transport_security"] is True
    assert body["max_file_mb"] == 8


def test_docs(mcp_client: TestClient) -> None:
    assert mcp_client.get("/docs").status_code == 200


def test_mcp_get_and_delete_answer_405(mcp_client: TestClient) -> None:
    assert mcp_client.get("/mcp").status_code == 405
    assert mcp_client.delete("/mcp").status_code == 405


def test_legacy_sse_answers_405_naming_mcp(mcp_client: TestClient) -> None:
    for method in ("GET", "POST", "DELETE"):
        resp = mcp_client.request(method, "/sse")
        assert resp.status_code == 405, method
        assert "/mcp" in resp.json()["message"]


def test_initialize_handshake(mcp_client: TestClient) -> None:
    result = initialize(mcp_client)
    assert result["serverInfo"]["name"] == "paper-boxing-mcp"
    assert result["serverInfo"]["version"] == VERSION


def test_cors_is_limited_to_the_allowlist(mcp_client: TestClient) -> None:
    allowed = mcp_client.options(
        "/health",
        headers={"Origin": "http://localhost", "Access-Control-Request-Method": "GET"},
    )
    assert allowed.headers.get("access-control-allow-origin") == "http://localhost"
    foreign = mcp_client.options(
        "/health",
        headers={"Origin": "https://evil.example.com", "Access-Control-Request-Method": "GET"},
    )
    assert "access-control-allow-origin" not in foreign.headers
