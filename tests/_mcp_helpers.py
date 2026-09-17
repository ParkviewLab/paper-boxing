# SPDX-FileCopyrightText: 2026 Gary Frattarola <garyf@parkviewlab.ai>
#
# SPDX-License-Identifier: MIT OR Apache-2.0

"""Helpers for MCP-over-HTTP tests: the JSON-RPC and SSE plumbing behind `/mcp`.

The MCP worker's tool tests build on these: `initialize` for the handshake,
`call_tool` for a `tools/call`, `list_tools` for `tools/list`; every call
carries the bearer token of the agent under test.
"""

from __future__ import annotations

import json
from typing import Any

from fastapi.testclient import TestClient

MCP_PATH = "/mcp"
PROTOCOL_VERSION = "2025-06-18"


def parse_sse(body: str) -> list[dict[str, Any]]:
    """Pull the `data: <json>` payloads out of a Streamable HTTP SSE response."""
    out: list[dict[str, Any]] = []
    for line in body.splitlines():
        if line.startswith("data:"):
            out.append(json.loads(line[5:].strip()))
    return out


def mcp_request(
    client: TestClient,
    method: str,
    params: dict[str, Any] | None = None,
    *,
    req_id: int = 1,
    token: str | None = None,
    extra_headers: dict[str, str] | None = None,
) -> tuple[int, dict[str, Any]]:
    """POST one JSON-RPC request to /mcp; return (status, last JSON-RPC message or error body)."""
    payload: dict[str, Any] = {"jsonrpc": "2.0", "id": req_id, "method": method}
    if params is not None:
        payload["params"] = params
    headers = {
        "Accept": "application/json, text/event-stream",
        "Content-Type": "application/json",
        **(extra_headers or {}),
    }
    if token is not None:
        headers["Authorization"] = f"Bearer {token}"
    resp = client.post(MCP_PATH, json=payload, headers=headers)
    content_type = resp.headers.get("content-type", "")
    if content_type.startswith("application/json"):
        return resp.status_code, resp.json()
    if content_type.startswith("text/event-stream"):
        messages = parse_sse(resp.text)
        return resp.status_code, messages[-1] if messages else {}
    return resp.status_code, {"raw": resp.text}


def initialize(client: TestClient, *, token: str | None = None) -> dict[str, Any]:
    """The MCP `initialize` handshake; returns the `result` object."""
    status, body = mcp_request(
        client,
        "initialize",
        {
            "protocolVersion": PROTOCOL_VERSION,
            "capabilities": {},
            "clientInfo": {"name": "pytest", "version": "0.0"},
        },
        token=token,
    )
    assert status == 200, (status, body)
    assert "result" in body, body
    return body["result"]


def list_tools(client: TestClient, *, token: str | None = None) -> list[dict[str, Any]]:
    status, body = mcp_request(client, "tools/list", {}, req_id=2, token=token)
    assert status == 200, (status, body)
    assert "result" in body, body
    return body["result"]["tools"]


def call_tool(
    client: TestClient, name: str, arguments: dict[str, Any], *, token: str | None = None, req_id: int = 100
) -> dict[str, Any]:
    """A `tools/call`; returns the `result` object (content, structuredContent, isError)."""
    status, body = mcp_request(
        client, "tools/call", {"name": name, "arguments": arguments}, req_id=req_id, token=token
    )
    assert status == 200, (status, body)
    assert "result" in body, body
    return body["result"]
