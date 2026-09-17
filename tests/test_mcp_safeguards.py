# SPDX-FileCopyrightText: 2026 Gary Frattarola <garyf@parkviewlab.ai>
#
# SPDX-License-Identifier: MIT OR Apache-2.0

"""The three safeguards against token passthrough (docs/design.md section 4a).

Every request's bearer is confirmed with the backend before a tool runs, and
nothing is kept between requests: the fake backend's request record
(`FakeState.requests`) is the proof, one `token_self` call per request under
the token that made it. An unknown, revoked or expired token gets 401, a
session token 403, both in the contract's error shape and with no tool run.
"""

from __future__ import annotations

import httpx
import pytest
from fastapi.testclient import TestClient
from starlette.datastructures import Headers

from paper_boxing.common.client import BackendClient
from paper_boxing.common.fake_backend import FakeState
from paper_boxing.common.schema import ErrorBody, ErrorCode
from paper_boxing.common.scopes import Scope
from paper_boxing.mcp import auth
from tests._mcp_helpers import MCP_PATH, call_tool, initialize, list_tools, mcp_request

INIT = {
    "protocolVersion": "2025-06-18",
    "capabilities": {},
    "clientInfo": {"name": "pytest", "version": "0.0"},
}


def _agent(fake_state: FakeState, scope: Scope, name: str = "agent") -> tuple[str, str]:
    """An agent token of `scope` for the bootstrap account; returns (token id, bearer secret)."""
    token, secret = fake_state.issue_agent_token("admin", name, scope)
    return token.id, secret


def _routes_since(fake_state: FakeState, start: int) -> list[tuple[str, str | None, str | None]]:
    return [(r.route, r.token_id, r.via) for r in fake_state.requests[start:]]


# ---------------------------------------------------------------------------
# Safeguard 2: only tokens the backend confirms


def test_missing_token_is_401_before_the_backend_or_a_tool_is_involved(
    mcp_client: TestClient, fake_state: FakeState
) -> None:
    start = len(fake_state.requests)
    for method, params in (("tools/list", {}), ("tools/call", {"name": "list_sites", "arguments": {}})):
        status, body = mcp_request(mcp_client, method, params)
        assert status == 401, (method, body)
        assert ErrorBody.model_validate(body).error.code is ErrorCode.UNAUTHORIZED
    assert _routes_since(fake_state, start) == [], "no backend call and no tool for a tokenless request"


def test_401_carries_a_bearer_challenge(mcp_client: TestClient) -> None:
    resp = mcp_client.post(
        MCP_PATH,
        json={"jsonrpc": "2.0", "id": 1, "method": "tools/list", "params": {}},
        headers={"Accept": "application/json, text/event-stream", "Content-Type": "application/json"},
    )
    assert resp.status_code == 401
    assert resp.headers["www-authenticate"].startswith("Bearer ")


def test_unknown_token_is_401_after_the_backend_declined_it(
    mcp_client: TestClient, fake_state: FakeState
) -> None:
    start = len(fake_state.requests)
    status, body = mcp_request(mcp_client, "tools/list", {}, token="pb_" + "x" * 43)
    assert status == 401, body
    assert ErrorBody.model_validate(body).error.code is ErrorCode.UNAUTHORIZED
    assert _routes_since(fake_state, start) == [("token_self", None, "mcp")]


def test_revoked_token_is_401_and_no_tool_runs(mcp_client: TestClient, fake_state: FakeState) -> None:
    token_id, secret = _agent(fake_state, Scope.REMOVE_DESTRUCTIVE, "soon-revoked")
    assert len(list_tools(mcp_client, token=secret)) == 8
    fake_state.revoke(token_id)
    start = len(fake_state.requests)
    status, body = mcp_request(
        mcp_client, "tools/call", {"name": "list_sites", "arguments": {}}, token=secret
    )
    assert status == 401, body
    assert ErrorBody.model_validate(body).error.code is ErrorCode.UNAUTHORIZED
    assert _routes_since(fake_state, start) == [("token_self", None, "mcp")]


def test_expired_session_is_401_not_403(mcp_client: TestClient, fake_state: FakeState) -> None:
    """An expired token is refused by the backend before its type is looked at."""
    session = fake_state.issue_session("admin")
    fake_state.clock.advance(15 * 24 * 3600)  # past the 14-day sliding expiry
    status, body = mcp_request(mcp_client, "tools/list", {}, token=session)
    assert status == 401, body
    assert ErrorBody.model_validate(body).error.code is ErrorCode.UNAUTHORIZED


def test_other_authorization_schemes_are_401(mcp_client: TestClient, fake_state: FakeState) -> None:
    _, secret = _agent(fake_state, Scope.READ_ONLY, "basic")
    status, body = mcp_request(
        mcp_client, "tools/list", {}, extra_headers={"Authorization": f"Basic {secret}"}
    )
    assert status == 401, body


# ---------------------------------------------------------------------------
# Safeguard 3: agent tokens only


def test_session_token_is_403_wrong_token_type_and_no_tool_runs(
    mcp_client: TestClient, fake_state: FakeState
) -> None:
    session = fake_state.issue_session("admin")
    start = len(fake_state.requests)
    for method, params in (("tools/list", {}), ("tools/call", {"name": "list_sites", "arguments": {}})):
        status, body = mcp_request(mcp_client, method, params, token=session)
        assert status == 403, (method, body)
        assert ErrorBody.model_validate(body).error.code is ErrorCode.WRONG_TOKEN_TYPE
    session_id = fake_state.secrets[session]
    assert _routes_since(fake_state, start) == [("token_self", session_id, "mcp")] * 2


# ---------------------------------------------------------------------------
# Safeguard 1: confirmed on every request, nothing cached


def test_token_is_confirmed_on_every_request_and_nothing_is_cached(
    mcp_client: TestClient, fake_state: FakeState
) -> None:
    a_id, a = _agent(fake_state, Scope.READ_ONLY, "a")
    b_id, b = _agent(fake_state, Scope.REMOVE_DESTRUCTIVE, "b")
    start = len(fake_state.requests)

    assert len(list_tools(mcp_client, token=a)) == 3
    assert len(list_tools(mcp_client, token=b)) == 8
    assert len(list_tools(mcp_client, token=a)) == 3, "the second token's confirmation was not reused"
    assert call_tool(mcp_client, "list_sites", {}, token=a)["isError"] is False

    confirmations = [r for r in _routes_since(fake_state, start) if r[0] == "token_self"]
    assert confirmations == [
        ("token_self", a_id, "mcp"),
        ("token_self", b_id, "mcp"),
        ("token_self", a_id, "mcp"),
        ("token_self", a_id, "mcp"),
    ], "one confirmation per request, under that request's own token"

    fake_state.revoke(a_id)
    status, body = mcp_request(mcp_client, "tools/list", {}, token=a)
    assert status == 401, ("a revocation takes effect on the very next request", body)
    assert _routes_since(fake_state, start)[-1] == ("token_self", None, "mcp")


def test_a_forwarded_call_carries_the_token_and_the_via_header(
    mcp_client: TestClient, fake_state: FakeState
) -> None:
    token_id, secret = _agent(fake_state, Scope.READ_ONLY, "via")
    start = len(fake_state.requests)
    result = call_tool(mcp_client, "list_sites", {}, token=secret)
    assert result["isError"] is False
    assert _routes_since(fake_state, start) == [
        ("token_self", token_id, "mcp"),
        ("list_sites", token_id, "mcp"),
    ]


def test_initialize_needs_no_token_but_a_token_on_it_is_confirmed(
    mcp_client: TestClient, fake_state: FakeState
) -> None:
    assert initialize(mcp_client)["serverInfo"]["name"] == "paper-boxing-mcp"
    status, body = mcp_request(mcp_client, "initialize", INIT, token="pb_" + "y" * 43)
    assert status == 401, body
    token_id, secret = _agent(fake_state, Scope.READ_ONLY, "init")
    start = len(fake_state.requests)
    assert initialize(mcp_client, token=secret)["serverInfo"]["name"] == "paper-boxing-mcp"
    assert _routes_since(fake_state, start) == [("token_self", token_id, "mcp")]


def test_a_tokenless_batch_that_names_a_tool_method_is_401(mcp_client: TestClient) -> None:
    resp = mcp_client.post(
        MCP_PATH,
        json=[
            {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": INIT},
            {"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}},
        ],
        headers={"Accept": "application/json, text/event-stream", "Content-Type": "application/json"},
    )
    assert resp.status_code == 401


# ---------------------------------------------------------------------------
# The gate's parts, on their own


@pytest.mark.parametrize(
    ("header", "expected"),
    [
        ("Bearer pb_abc", "pb_abc"),
        ("bearer pb_abc", "pb_abc"),
        ("Bearer   pb_abc  ", "pb_abc"),
        ("Bearer", None),
        ("Bearer ", None),
        ("Basic pb_abc", None),
        ("", None),
    ],
)
def test_bearer_from(header: str, expected: str | None) -> None:
    headers = Headers({"authorization": header}) if header else Headers({})
    assert auth.bearer_from(headers) == expected


@pytest.mark.parametrize(
    ("body", "expected"),
    [
        (b'{"jsonrpc":"2.0","id":1,"method":"initialize","params":{}}', False),
        (b'{"jsonrpc":"2.0","method":"notifications/initialized"}', False),
        (b'{"jsonrpc":"2.0","id":1,"method":"ping"}', False),
        (b'{"jsonrpc":"2.0","id":1,"method":"tools/list"}', True),
        (b'{"jsonrpc":"2.0","id":1,"method":"tools/call","params":{"name":"list_sites"}}', True),
        (b'{"jsonrpc":"2.0","id":1,"method":"prompts/list"}', True),
        (b'[{"method":"initialize"},{"method":"tools/list"}]', True),
        (b'[{"method":"initialize"},{"method":"ping"}]', False),
        (b'{"jsonrpc":"2.0","id":1,"result":{}}', False),
        (b"not json", False),
        (b"", False),
    ],
)
def test_needs_token(body: bytes, expected: bool) -> None:
    assert auth.needs_token(body) is expected


async def test_confirm_reports_an_unreachable_backend_as_503() -> None:
    def down(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("connection refused", request=request)

    async with httpx.AsyncClient(transport=httpx.MockTransport(down), base_url="http://backend") as http:
        with pytest.raises(auth.AuthRefused) as exc:
            await auth.confirm(BackendClient(http, via="mcp"), "pb_x")
    assert exc.value.status == 503
    assert exc.value.code == auth.BACKEND_UNREACHABLE


async def test_confirm_passes_another_backend_failure_through_in_shape() -> None:
    def broken(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500, json={"error": {"code": "internal_error", "message": "boom"}})

    async with httpx.AsyncClient(transport=httpx.MockTransport(broken), base_url="http://backend") as http:
        with pytest.raises(auth.AuthRefused) as exc:
            await auth.confirm(BackendClient(http, via="mcp"), "pb_x")
    assert (exc.value.status, exc.value.code) == (500, "internal_error")
    assert "boom" in exc.value.message


def test_oversized_request_body_is_413_in_the_contract_shape(
    mcp_client: TestClient, fake_state: FakeState
) -> None:
    from paper_boxing.mcp import server

    _, secret = _agent(fake_state, Scope.READ_WRITE, "big")
    limit = server.session_manager.max_request_body_size
    resp = mcp_client.post(
        MCP_PATH,
        content=b"x" * (limit + 1),
        headers={
            "Accept": "application/json, text/event-stream",
            "Content-Type": "application/json",
            "Authorization": f"Bearer {secret}",
        },
    )
    assert resp.status_code == 413, resp.text
    assert ErrorBody.model_validate(resp.json()).error.code is ErrorCode.PAYLOAD_TOO_LARGE
