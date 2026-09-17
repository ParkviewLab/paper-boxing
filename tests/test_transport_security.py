# SPDX-FileCopyrightText: 2026 Gary Frattarola <garyf@parkviewlab.ai>
#
# SPDX-License-Identifier: MIT OR Apache-2.0

"""Transport security (DNS-rebinding protection: Host and Origin allowlist).

`server.py` passes explicit security settings, so /mcp rejects a foreign Host
(421) and a foreign browser Origin (403), each in the contract's error body,
while a loopback, no-Origin MCP client with an agent token passes.
"""

from __future__ import annotations

from fastapi.testclient import TestClient

from paper_boxing.common.fake_backend import FakeState
from paper_boxing.common.schema import ErrorBody, ErrorCode
from paper_boxing.common.scopes import Scope
from tests._mcp_helpers import mcp_request

_INIT = {
    "protocolVersion": "2025-06-18",
    "capabilities": {},
    "clientInfo": {"name": "pytest", "version": "0.0"},
}


def test_security_settings_enabled(mcp_client: TestClient) -> None:
    from paper_boxing.mcp import server

    settings = server.session_manager.security_settings
    assert settings is not None
    assert settings.enable_dns_rebinding_protection is True
    assert "*" not in server.cfg.allowed_origins


def test_foreign_host_rejected(mcp_client: TestClient) -> None:
    status, body = mcp_request(mcp_client, "initialize", _INIT, extra_headers={"Host": "evil.example.com"})
    assert status == 421
    assert ErrorBody.model_validate(body).error.code is ErrorCode.FORBIDDEN


def test_foreign_origin_rejected(mcp_client: TestClient) -> None:
    status, body = mcp_request(
        mcp_client, "initialize", _INIT, extra_headers={"Origin": "https://evil.example.com"}
    )
    assert status == 403
    assert ErrorBody.model_validate(body).error.code is ErrorCode.FORBIDDEN


def test_loopback_no_origin_allowed(mcp_client: TestClient, fake_state: FakeState) -> None:
    _, secret = fake_state.issue_agent_token("admin", "loopback", Scope.READ_ONLY)
    status, body = mcp_request(mcp_client, "initialize", _INIT, token=secret)
    assert status == 200
    assert "result" in body
