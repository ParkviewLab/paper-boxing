# SPDX-FileCopyrightText: 2026 Gary Frattarola <garyf@parkviewlab.ai>
#
# SPDX-License-Identifier: MIT OR Apache-2.0

"""The three safeguards against token passthrough (docs/architecture.md, "The MCP server"), as `auth.py` keeps them.

Every POST to /mcp needs a bearer, the handshake included, and every bearer
is confirmed with the backend before a tool runs; nothing is kept between
requests. The fake backend's request record (`FakeState.requests`) is the
proof: one `token_self` per request under the token that made it, and none
for a request the gate refuses without asking. An unknown, revoked or expired
token gets 401, a session token 403, a backend that cannot confirm 503, a
foreign Host 421, a foreign Origin 403, a wrong Content-Type 400 and an
oversized body 413; every one in the contract's error shape.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from typing import Any

import httpx
import pytest
from fastapi.testclient import TestClient
from mcp.server.transport_security import TransportSecuritySettings
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
HEADERS = {"Accept": "application/json, text/event-stream", "Content-Type": "application/json"}


def _agent(fake_state: FakeState, scope: Scope, name: str = "agent") -> tuple[str, str]:
    """An agent token of `scope` for the bootstrap account; returns (token id, bearer secret)."""
    token, secret = fake_state.issue_agent_token("admin", name, scope)
    return token.id, secret


def _routes_since(fake_state: FakeState, start: int) -> list[tuple[str, str | None, str | None]]:
    return [(r.route, r.token_id, r.via) for r in fake_state.requests[start:]]


def _shaped(body: dict[str, Any], code: ErrorCode) -> None:
    assert ErrorBody.model_validate(body).error.code is code, body


# ---------------------------------------------------------------------------
# Safeguard 2: only tokens the backend confirms, on every request


def test_every_request_needs_a_bearer_including_the_handshake(
    mcp_client: TestClient, fake_state: FakeState
) -> None:
    start = len(fake_state.requests)
    for method, params in (
        ("initialize", INIT),
        ("tools/list", {}),
        ("tools/call", {"name": "list_sites", "arguments": {}}),
        ("ping", {}),
    ):
        status, body = mcp_request(mcp_client, method, params)
        assert status == 401, (method, body)
        _shaped(body, ErrorCode.UNAUTHORIZED)
        assert body["error"]["message"] == auth.MSG_MISSING
    assert _routes_since(fake_state, start) == [], "no backend call and no tool for a tokenless request"


def test_401_carries_a_bearer_challenge(mcp_client: TestClient) -> None:
    resp = mcp_client.post(
        MCP_PATH, json={"jsonrpc": "2.0", "id": 1, "method": "tools/list", "params": {}}, headers=HEADERS
    )
    assert resp.status_code == 401
    assert resp.headers["www-authenticate"] == auth.CHALLENGE_MISSING["WWW-Authenticate"]


def test_unknown_token_is_401_with_a_fixed_message(mcp_client: TestClient, fake_state: FakeState) -> None:
    start = len(fake_state.requests)
    status, body = mcp_request(mcp_client, "tools/list", {}, token="pb_" + "x" * 43)
    assert status == 401, body
    _shaped(body, ErrorCode.UNAUTHORIZED)
    assert body["error"]["message"] == auth.MSG_NOT_CONFIRMED, "nothing of the backend's answer is spliced in"
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
    _shaped(body, ErrorCode.UNAUTHORIZED)
    assert _routes_since(fake_state, start) == [("token_self", None, "mcp")]


def test_expired_session_is_401_not_403(mcp_client: TestClient, fake_state: FakeState) -> None:
    """An expired token is refused by the backend before its type is looked at."""
    session = fake_state.issue_session("admin")
    fake_state.clock.advance(15 * 24 * 3600)  # past the 14-day sliding expiry
    status, body = mcp_request(mcp_client, "tools/list", {}, token=session)
    assert status == 401, body
    _shaped(body, ErrorCode.UNAUTHORIZED)


def test_other_authorization_schemes_are_401(mcp_client: TestClient, fake_state: FakeState) -> None:
    _, secret = _agent(fake_state, Scope.READ_ONLY, "basic")
    start = len(fake_state.requests)
    status, body = mcp_request(
        mcp_client, "tools/list", {}, extra_headers={"Authorization": f"Basic {secret}"}
    )
    assert status == 401, body
    assert _routes_since(fake_state, start) == []


# ---------------------------------------------------------------------------
# Safeguard 3: agent tokens only


def test_session_token_is_403_wrong_token_type_and_no_tool_runs(
    mcp_client: TestClient, fake_state: FakeState
) -> None:
    session = fake_state.issue_session("admin")
    start = len(fake_state.requests)
    for method, params in (
        ("initialize", INIT),
        ("tools/list", {}),
        ("tools/call", {"name": "list_sites", "arguments": {}}),
    ):
        status, body = mcp_request(mcp_client, method, params, token=session)
        assert status == 403, (method, body)
        _shaped(body, ErrorCode.WRONG_TOKEN_TYPE)
    session_id = fake_state.secrets[session]
    assert _routes_since(fake_state, start) == [("token_self", session_id, "mcp")] * 3


# ---------------------------------------------------------------------------
# Safeguard 1: confirmed on every request, nothing cached


def test_token_is_confirmed_on_every_request_and_nothing_is_cached(
    mcp_client: TestClient, fake_state: FakeState
) -> None:
    a_id, a = _agent(fake_state, Scope.READ_ONLY, "a")
    b_id, b = _agent(fake_state, Scope.REMOVE_DESTRUCTIVE, "b")
    start = len(fake_state.requests)

    assert initialize(mcp_client, token=a)["serverInfo"]["name"] == "paper-boxing-mcp"
    assert len(list_tools(mcp_client, token=a)) == 3
    assert len(list_tools(mcp_client, token=b)) == 8
    assert len(list_tools(mcp_client, token=a)) == 3, "the second token's confirmation was not reused"
    assert call_tool(mcp_client, "list_sites", {}, token=a)["isError"] is False

    confirmations = [r for r in _routes_since(fake_state, start) if r[0] == "token_self"]
    assert confirmations == [
        ("token_self", a_id, "mcp"),
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


# ---------------------------------------------------------------------------
# What is refused before the backend is asked: transport security and the declared size


def test_foreign_host_is_421_in_shape_before_any_backend_call(
    mcp_client: TestClient, fake_state: FakeState
) -> None:
    _, secret = _agent(fake_state, Scope.READ_ONLY, "host")
    start = len(fake_state.requests)
    status, body = mcp_request(
        mcp_client, "tools/list", {}, token=secret, extra_headers={"Host": "evil.example.com"}
    )
    assert status == 421, body
    _shaped(body, ErrorCode.FORBIDDEN)
    assert "PAPER_BOXING_MCP_ALLOWED_HOSTS" in body["error"]["message"]
    assert _routes_since(fake_state, start) == [], "a rebinding refusal costs no confirmation"


def test_foreign_origin_is_403_in_shape_before_any_backend_call(
    mcp_client: TestClient, fake_state: FakeState
) -> None:
    _, secret = _agent(fake_state, Scope.READ_ONLY, "origin")
    start = len(fake_state.requests)
    status, body = mcp_request(
        mcp_client, "tools/list", {}, token=secret, extra_headers={"Origin": "https://evil.example.com"}
    )
    assert status == 403, body
    _shaped(body, ErrorCode.FORBIDDEN)
    assert "PAPER_BOXING_MCP_ALLOWED_ORIGINS" in body["error"]["message"]
    assert _routes_since(fake_state, start) == []


def test_wrong_content_type_is_400_in_shape_before_any_backend_call(
    mcp_client: TestClient, fake_state: FakeState
) -> None:
    _, secret = _agent(fake_state, Scope.READ_ONLY, "ctype")
    start = len(fake_state.requests)
    status, body = mcp_request(
        mcp_client, "tools/list", {}, token=secret, extra_headers={"Content-Type": "text/plain"}
    )
    assert status == 400, body
    _shaped(body, ErrorCode.BAD_REQUEST)
    assert _routes_since(fake_state, start) == []


def test_declared_oversized_body_is_413_in_shape_before_any_backend_call(
    mcp_client: TestClient, fake_state: FakeState
) -> None:
    from paper_boxing.mcp import server

    _, secret = _agent(fake_state, Scope.READ_WRITE, "big")
    start = len(fake_state.requests)
    limit = server.session_manager.max_request_body_size
    resp = mcp_client.post(
        MCP_PATH, content=b"x" * (limit + 1), headers={**HEADERS, "Authorization": f"Bearer {secret}"}
    )
    assert resp.status_code == 413, resp.text
    _shaped(resp.json(), ErrorCode.PAYLOAD_TOO_LARGE)
    assert _routes_since(fake_state, start) == []


# ---------------------------------------------------------------------------
# `confirm` on its own: never raises anything but AuthRefused, never reflects the backend


def _backend(handler: Callable[[httpx.Request], httpx.Response]) -> BackendClient:
    return BackendClient(
        httpx.AsyncClient(transport=httpx.MockTransport(handler), base_url="http://backend"), via="mcp"
    )


def _token_self(token_type: str) -> httpx.Response:
    return httpx.Response(
        200,
        json={
            "id": "tok_1",
            "name": "t",
            "type": token_type,
            "scope": "read_only",
            "username": "admin",
            "expires_at": None,
        },
    )


async def test_confirm_accepts_an_agent_token() -> None:
    confirmed = await auth.confirm(_backend(lambda r: _token_self("agent")), "pb_x")
    assert confirmed.bearer == "pb_x" and confirmed.token.scope is Scope.READ_ONLY


async def test_confirm_refuses_a_session_token_with_403() -> None:
    with pytest.raises(auth.AuthRefused) as exc:
        await auth.confirm(_backend(lambda r: _token_self("session")), "pb_x")
    assert (exc.value.status, exc.value.code) == (403, ErrorCode.WRONG_TOKEN_TYPE)


async def test_confirm_keeps_a_backend_401_as_401() -> None:
    def declined(request: httpx.Request) -> httpx.Response:
        return httpx.Response(401, json={"error": {"code": "unauthorized", "message": "secret detail"}})

    with pytest.raises(auth.AuthRefused) as exc:
        await auth.confirm(_backend(declined), "pb_x")
    assert (exc.value.status, exc.value.code) == (401, ErrorCode.UNAUTHORIZED)
    assert exc.value.message == auth.MSG_NOT_CONFIRMED
    assert exc.value.headers == auth.CHALLENGE_INVALID


@pytest.mark.parametrize(
    "answer",
    [
        pytest.param(
            lambda r: (_ for _ in ()).throw(httpx.ConnectError("refused", request=r)), id="unreachable"
        ),
        pytest.param(
            lambda r: httpx.Response(500, json={"error": {"code": "internal_error", "message": "boom"}}),
            id="500",
        ),
        pytest.param(
            lambda r: httpx.Response(403, json={"error": {"code": "forbidden", "message": "no"}}), id="403"
        ),
        pytest.param(lambda r: httpx.Response(502, text="<html>bad gateway</html>"), id="502-html"),
        pytest.param(lambda r: httpx.Response(200, text="not json at all"), id="200-not-json"),
        pytest.param(lambda r: httpx.Response(200, json={"unexpected": True}), id="200-wrong-shape"),
    ],
)
async def test_confirm_answers_503_when_the_backend_cannot_confirm(
    answer: Callable[[httpx.Request], httpx.Response],
) -> None:
    with pytest.raises(auth.AuthRefused) as exc:
        await auth.confirm(_backend(answer), "pb_x")
    assert (exc.value.status, exc.value.code) == (503, ErrorCode.BACKEND_UNREACHABLE)
    assert exc.value.message == auth.MSG_UNCONFIRMED
    for leaked in ("boom", "bad gateway", "not json", "unexpected"):
        assert leaked not in exc.value.message


# ---------------------------------------------------------------------------
# The gate on its own, driven as a bare ASGI app


def _scope(method: str, headers: dict[str, str]) -> dict[str, Any]:
    return {
        "type": "http",
        "asgi": {"version": "3.0"},
        "http_version": "1.1",
        "method": method,
        "scheme": "http",
        "path": "/mcp",
        "raw_path": b"/mcp",
        "query_string": b"",
        "root_path": "",
        "headers": [(k.lower().encode(), v.encode()) for k, v in headers.items()],
        "client": ("127.0.0.1", 1),
        "server": ("127.0.0.1", 35842),
        "state": {},
    }


async def _drive(gate: auth.TokenGate, scope: dict[str, Any]) -> tuple[int, dict[str, Any]]:
    sent: list[dict[str, Any]] = []

    async def receive() -> dict[str, Any]:
        return {"type": "http.request", "body": b"{}", "more_body": False}

    async def send(message: dict[str, Any]) -> None:
        sent.append(message)

    await gate(scope, receive, send)
    status = next(m["status"] for m in sent if m["type"] == "http.response.start")
    body = b"".join(m.get("body", b"") for m in sent if m["type"] == "http.response.body")
    return status, json.loads(body)


def _gate(backend: Callable[[], BackendClient], inner_calls: list[dict[str, Any]]) -> auth.TokenGate:
    async def inner(scope: Any, receive: Any, send: Any) -> None:
        inner_calls.append(scope)
        await send({"type": "http.response.start", "status": 200, "headers": []})
        await send({"type": "http.response.body", "body": b'{"inner": true}', "more_body": False})

    security = TransportSecuritySettings(
        enable_dns_rebinding_protection=True, allowed_hosts=["localhost"], allowed_origins=[]
    )
    return auth.TokenGate(inner, backend, security=security, max_body_bytes=1024)


POST_HEADERS = {"Host": "localhost", "Content-Type": "application/json"}


async def test_gate_answers_405_in_shape_for_another_method() -> None:
    calls: list[dict[str, Any]] = []
    status, body = await _drive(
        _gate(lambda: _backend(lambda r: _token_self("agent")), calls), _scope("GET", POST_HEADERS)
    )
    assert status == 405
    _shaped(body, ErrorCode.METHOD_NOT_ALLOWED)
    assert calls == []


async def test_gate_never_raises_when_the_backend_client_is_unavailable() -> None:
    def no_backend() -> BackendClient:
        raise RuntimeError("lifespan not started")

    calls: list[dict[str, Any]] = []
    status, body = await _drive(
        _gate(no_backend, calls), _scope("POST", {**POST_HEADERS, "Authorization": "Bearer pb_x"})
    )
    assert status == 503
    _shaped(body, ErrorCode.BACKEND_UNREACHABLE)
    assert calls == []


async def test_gate_passes_a_confirmed_agent_token_to_the_transport_with_its_confirmation() -> None:
    calls: list[dict[str, Any]] = []
    gate = _gate(lambda: _backend(lambda r: _token_self("agent")), calls)
    status, body = await _drive(gate, _scope("POST", {**POST_HEADERS, "Authorization": "Bearer pb_x"}))
    assert (status, body) == (200, {"inner": True})
    confirmed = calls[0]["state"][auth.STATE_KEY]
    assert isinstance(confirmed, auth.Confirmed)
    assert confirmed.bearer == "pb_x" and confirmed.token.username == "admin"


def test_security_settings_are_required() -> None:
    with pytest.raises(TypeError):
        auth.TokenGate(lambda s, r, x: None, lambda: None, max_body_bytes=1)  # type: ignore[call-arg]


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
