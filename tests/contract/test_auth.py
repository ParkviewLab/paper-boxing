# SPDX-FileCopyrightText: 2026 Gary Frattarola <garyf@parkviewlab.ai>
#
# SPDX-License-Identifier: MIT OR Apache-2.0

"""Authentication: login, logout, bearer validation, session expiry."""

from __future__ import annotations

from fastapi.testclient import TestClient

from paper_boxing.backend.clock import Clock
from paper_boxing.common.schema import ErrorBody, ErrorCode, LoginResponse, TokenSelf
from tests.contract.conftest import ADMIN, Actors, bearer


def _error(resp) -> ErrorCode:
    return ErrorBody.model_validate(resp.json()).error.code


def test_login_returns_a_session(api: TestClient) -> None:
    resp = api.post("/api/v1/auth/login", json={"username": ADMIN[0], "password": ADMIN[1]})
    assert resp.status_code == 200
    login = LoginResponse.model_validate(resp.json())
    assert login.user.username == ADMIN[0]
    me = TokenSelf.model_validate(api.get("/api/v1/tokens/self", headers=bearer(login.token)).json())
    assert me.type == "session"
    assert me.scope == "remove_destructive"
    assert me.username == ADMIN[0]
    assert me.expires_at == login.expires_at or me.expires_at >= login.expires_at


def test_login_failure_is_401(api: TestClient) -> None:
    for creds in ({"username": ADMIN[0], "password": "wrong"}, {"username": "nobody", "password": "x"}):
        resp = api.post("/api/v1/auth/login", json=creds)
        assert resp.status_code == 401
        assert _error(resp) is ErrorCode.INVALID_CREDENTIALS


def test_login_body_is_validated(api: TestClient) -> None:
    resp = api.post("/api/v1/auth/login", json={"username": ""})
    assert resp.status_code == 422
    assert _error(resp) is ErrorCode.VALIDATION_ERROR


def test_missing_and_malformed_bearer_are_401(api: TestClient) -> None:
    assert api.get("/api/v1/sites").status_code == 401
    assert _error(api.get("/api/v1/sites")) is ErrorCode.UNAUTHORIZED
    assert api.get("/api/v1/sites", headers={"Authorization": "Basic abc"}).status_code == 401
    assert api.get("/api/v1/sites", headers={"Authorization": "Bearer "}).status_code == 401
    assert api.get("/api/v1/sites", headers=bearer("pb_unknown")).status_code == 401


def test_revoked_token_is_401(api: TestClient, actors: Actors) -> None:
    resp = api.get("/api/v1/tokens/self", headers=bearer(actors.revoked))
    assert resp.status_code == 401
    assert _error(resp) is ErrorCode.UNAUTHORIZED


def test_logout_ends_the_session(api: TestClient, actors: Actors) -> None:
    assert api.post("/api/v1/auth/logout", headers=bearer(actors.session)).status_code == 204
    assert api.get("/api/v1/tokens/self", headers=bearer(actors.session)).status_code == 401


def test_agent_token_cannot_log_out(api: TestClient, actors: Actors) -> None:
    resp = api.post("/api/v1/auth/logout", headers=bearer(actors.remove_destructive))
    assert resp.status_code == 403
    assert _error(resp) is ErrorCode.SESSION_REQUIRED


def test_session_expiry_is_sliding(api: TestClient, actors: Actors, clock: Clock) -> None:
    day = 24 * 3600
    clock.advance(10 * day)
    assert api.get("/api/v1/tokens/self", headers=bearer(actors.session)).status_code == 200  # extends it
    clock.advance(10 * day)
    assert api.get("/api/v1/tokens/self", headers=bearer(actors.session)).status_code == 200
    clock.advance(15 * day)
    resp = api.get("/api/v1/tokens/self", headers=bearer(actors.session))
    assert resp.status_code == 401
    assert _error(resp) is ErrorCode.UNAUTHORIZED


def test_agent_tokens_do_not_expire(api: TestClient, actors: Actors, clock: Clock) -> None:
    clock.advance(400 * 24 * 3600)
    me = TokenSelf.model_validate(api.get("/api/v1/tokens/self", headers=bearer(actors.read_only)).json())
    assert me.type == "agent"
    assert me.scope == "read_only"
    assert me.expires_at is None


def test_health_and_version_need_no_token(api: TestClient) -> None:
    assert api.get("/health").status_code == 200
    assert api.get("/admin/version").status_code == 200


def test_error_bodies_have_the_contract_shape(api: TestClient, actors: Actors) -> None:
    for resp in (
        api.get("/api/v1/nowhere", headers=bearer(actors.session)),
        api.patch("/api/v1/sites", headers=bearer(actors.session)),
        api.post("/api/v1/sites", json={"nope": 1}, headers=bearer(actors.session)),
    ):
        body = ErrorBody.model_validate(resp.json())
        assert body.error.message
    assert api.get("/api/v1/nowhere", headers=bearer(actors.session)).status_code == 404
    assert api.patch("/api/v1/sites", headers=bearer(actors.session)).status_code == 405
