# SPDX-FileCopyrightText: 2026 Gary Frattarola <garyf@parkviewlab.ai>
#
# SPDX-License-Identifier: MIT OR Apache-2.0

"""Transport security (DNS-rebinding protection: Host and Origin allowlist).

`server.py` passes explicit security settings, so /mcp rejects a foreign Host
(421) and a foreign browser Origin (403) while a loopback, no-Origin MCP
client passes.
"""

from __future__ import annotations

from fastapi.testclient import TestClient

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
    status, _ = mcp_request(mcp_client, "initialize", _INIT, extra_headers={"Host": "evil.example.com"})
    assert status == 421


def test_foreign_origin_rejected(mcp_client: TestClient) -> None:
    status, _ = mcp_request(
        mcp_client, "initialize", _INIT, extra_headers={"Origin": "https://evil.example.com"}
    )
    assert status == 403


def test_loopback_no_origin_allowed(mcp_client: TestClient) -> None:
    status, body = mcp_request(mcp_client, "initialize", _INIT)
    assert status == 200
    assert "result" in body
