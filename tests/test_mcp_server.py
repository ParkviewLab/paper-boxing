# SPDX-FileCopyrightText: 2026 Gary Frattarola <garyf@parkviewlab.ai>
#
# SPDX-License-Identifier: MIT OR Apache-2.0

"""The MCP server's transport plumbing: the ops endpoints, the /mcp verbs, the
legacy /sse path, every HTTP error in the contract's body, and a successful
initialize handshake through /mcp with an agent token."""

from __future__ import annotations

from fastapi.testclient import TestClient

from paper_boxing.common.config import VERSION
from paper_boxing.common.fake_backend import FakeState
from paper_boxing.common.schema import ErrorBody, ErrorCode, Health
from paper_boxing.common.scopes import Scope
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


def test_mcp_get_and_delete_answer_405_in_the_contract_shape(mcp_client: TestClient) -> None:
    for method in ("GET", "DELETE"):
        resp = mcp_client.request(method, "/mcp")
        assert resp.status_code == 405, method
        assert ErrorBody.model_validate(resp.json()).error.code is ErrorCode.METHOD_NOT_ALLOWED


def test_legacy_sse_answers_405_naming_mcp(mcp_client: TestClient) -> None:
    for method in ("GET", "POST", "DELETE"):
        resp = mcp_client.request(method, "/sse")
        assert resp.status_code == 405, method
        body = ErrorBody.model_validate(resp.json())
        assert body.error.code is ErrorCode.METHOD_NOT_ALLOWED
        assert "/mcp" in body.error.message


def test_unknown_path_is_404_in_the_contract_shape(mcp_client: TestClient) -> None:
    resp = mcp_client.get("/nowhere")
    assert resp.status_code == 404
    assert ErrorBody.model_validate(resp.json()).error.code is ErrorCode.NOT_FOUND


def test_initialize_handshake(mcp_client: TestClient, fake_state: FakeState) -> None:
    _, secret = fake_state.issue_agent_token("admin", "handshake", Scope.READ_ONLY)
    result = initialize(mcp_client, token=secret)
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
