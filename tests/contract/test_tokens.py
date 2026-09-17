# SPDX-FileCopyrightText: 2026 Gary Frattarola <garyf@parkviewlab.ai>
#
# SPDX-License-Identifier: MIT OR Apache-2.0

"""Access tokens: creation (shown once), listing, revocation, self-inspection, and what an agent token may not do."""

from __future__ import annotations

from fastapi.testclient import TestClient

from paper_boxing.backend.clock import Clock
from paper_boxing.common.fake_backend import FakeState
from paper_boxing.common.schema import ErrorBody, ErrorCode, TokenCreated, TokenList, TokenSelf
from tests.contract.conftest import ADMIN, Actors, bearer


def _code(resp) -> ErrorCode:
    return ErrorBody.model_validate(resp.json()).error.code


def test_create_token_shows_the_secret_once(api: TestClient, actors: Actors) -> None:
    s = bearer(actors.session)
    resp = api.post("/api/v1/tokens", json={"name": "claude", "scope": "read_write"}, headers=s)
    assert resp.status_code == 201, resp.text
    created = TokenCreated.model_validate(resp.json())
    assert created.secret.startswith("pb_")
    assert created.token.type == "agent"
    assert created.token.scope == "read_write"
    assert created.token.username == ADMIN[0]
    assert created.token.expires_at is None
    listed = TokenList.model_validate(api.get("/api/v1/tokens", headers=s).json()).tokens
    mine = [t for t in listed if t.id == created.token.id]
    assert len(mine) == 1
    assert "secret" not in api.get("/api/v1/tokens", headers=s).text
    me = TokenSelf.model_validate(api.get("/api/v1/tokens/self", headers=bearer(created.secret)).json())
    assert me.id == created.token.id
    assert me.scope == "read_write"


def test_invalid_scope_is_422(api: TestClient, actors: Actors) -> None:
    resp = api.post("/api/v1/tokens", json={"name": "x", "scope": "admin"}, headers=bearer(actors.session))
    assert resp.status_code == 422
    assert _code(resp) is ErrorCode.VALIDATION_ERROR


def test_list_shows_agent_tokens_only(api: TestClient, actors: Actors) -> None:
    listed = TokenList.model_validate(api.get("/api/v1/tokens", headers=bearer(actors.session)).json()).tokens
    assert all(t.type == "agent" for t in listed)
    assert {t.name for t in listed} == {"ro", "rw", "rd"}  # the revoked one is gone


def test_revoke(api: TestClient, actors: Actors) -> None:
    s = bearer(actors.session)
    assert api.get("/api/v1/tokens/self", headers=bearer(actors.read_only)).status_code == 200
    assert api.delete(f"/api/v1/tokens/{actors.read_only_id}", headers=s).status_code == 204
    assert api.get("/api/v1/tokens/self", headers=bearer(actors.read_only)).status_code == 401
    assert api.delete(f"/api/v1/tokens/{actors.read_only_id}", headers=s).status_code == 404
    assert api.delete("/api/v1/tokens/tok_nowhere", headers=s).status_code == 404


def test_agent_tokens_cannot_manage_tokens_or_users(api: TestClient, actors: Actors) -> None:
    a = bearer(actors.remove_destructive)
    for resp in (
        api.get("/api/v1/tokens", headers=a),
        api.post("/api/v1/tokens", json={"name": "x", "scope": "read_only"}, headers=a),
        api.delete(f"/api/v1/tokens/{actors.read_only_id}", headers=a),
        api.get("/api/v1/users", headers=a),
        api.post("/api/v1/users", json={"username": "x", "password": "password-1"}, headers=a),
        api.post("/api/v1/users/self/password", json={"current": ADMIN[1], "new": "password-2"}, headers=a),
    ):
        assert resp.status_code == 403, resp.text
        assert _code(resp) is ErrorCode.SESSION_REQUIRED


def test_last_used_is_recorded(api: TestClient, actors: Actors, clock: Clock) -> None:
    s = bearer(actors.session)
    before = TokenList.model_validate(api.get("/api/v1/tokens", headers=s).json()).tokens
    ro = next(t for t in before if t.id == actors.read_only_id)
    assert ro.last_used_at is None
    clock.advance(60)
    api.get("/api/v1/sites", headers=bearer(actors.read_only))
    after = TokenList.model_validate(api.get("/api/v1/tokens", headers=s).json()).tokens
    ro = next(t for t in after if t.id == actors.read_only_id)
    assert ro.last_used_at is not None


def test_every_request_is_recorded_with_its_token(api: TestClient, actors: Actors, fake: FakeState) -> None:
    """The fake records each call with the token that made it and the Via header, so an MCP test can prove
    the token was confirmed on every request."""
    api.get("/api/v1/tokens/self", headers={**bearer(actors.read_only), "X-Paper-Boxing-Via": "mcp"})
    api.get("/api/v1/sites", headers={**bearer(actors.read_only), "X-Paper-Boxing-Via": "mcp"})
    recorded = fake.requests_for(actors.read_only_id)
    assert [(r.route, r.via) for r in recorded] == [("token_self", "mcp"), ("list_sites", "mcp")]
