# SPDX-FileCopyrightText: 2026 Gary Frattarola <garyf@parkviewlab.ai>
#
# SPDX-License-Identifier: MIT OR Apache-2.0

"""Accounts: equal rights, the last-account guard, password changes, and token clean-up."""

from __future__ import annotations

from fastapi.testclient import TestClient

from paper_boxing.common.schema import ErrorBody, ErrorCode, User, UserList
from tests.contract.conftest import ADMIN, Actors, bearer


def _code(resp) -> ErrorCode:
    return ErrorBody.model_validate(resp.json()).error.code


def test_create_list_delete(api: TestClient, actors: Actors) -> None:
    s = bearer(actors.session)
    resp = api.post("/api/v1/users", json={"username": "second", "password": "second-password"}, headers=s)
    assert resp.status_code == 201, resp.text
    user = User.model_validate(resp.json())
    assert user.username == "second"
    listed = UserList.model_validate(api.get("/api/v1/users", headers=s).json()).users
    assert [u.username for u in listed] == ["admin", "second"]
    assert api.delete(f"/api/v1/users/{user.id}", headers=s).status_code == 204
    assert api.delete(f"/api/v1/users/{user.id}", headers=s).status_code == 404
    assert [
        u.username for u in UserList.model_validate(api.get("/api/v1/users", headers=s).json()).users
    ] == ["admin"]


def test_duplicate_and_invalid_users(api: TestClient, actors: Actors) -> None:
    s = bearer(actors.session)
    resp = api.post("/api/v1/users", json={"username": ADMIN[0], "password": "another-password"}, headers=s)
    assert resp.status_code == 409
    assert _code(resp) is ErrorCode.USER_EXISTS
    for body in (
        {"username": "bad name", "password": "long-enough"},
        {"username": "ok", "password": "short"},
    ):
        resp = api.post("/api/v1/users", json=body, headers=s)
        assert resp.status_code == 422, body
        assert _code(resp) is ErrorCode.VALIDATION_ERROR


def test_last_account_cannot_be_deleted(api: TestClient, actors: Actors) -> None:
    s = bearer(actors.session)
    admin = next(u for u in UserList.model_validate(api.get("/api/v1/users", headers=s).json()).users)
    resp = api.delete(f"/api/v1/users/{admin.id}", headers=s)
    assert resp.status_code == 409
    assert _code(resp) is ErrorCode.LAST_USER


def test_every_account_has_equal_rights(api: TestClient, actors: Actors) -> None:
    s = bearer(actors.session)
    api.post("/api/v1/users", json={"username": "second", "password": "second-password"}, headers=s)
    login = api.post("/api/v1/auth/login", json={"username": "second", "password": "second-password"})
    second = bearer(login.json()["token"])
    assert (
        api.post(
            "/api/v1/users", json={"username": "third", "password": "third-password"}, headers=second
        ).status_code
        == 201
    )
    assert (
        api.post("/api/v1/tokens", json={"name": "t", "scope": "read_only"}, headers=second).status_code
        == 201
    )
    # the second user sees, and may revoke, the admin's tokens: equal rights, no roles
    assert api.delete(f"/api/v1/tokens/{actors.read_only_id}", headers=second).status_code == 204


def test_deleting_a_user_revokes_their_tokens(api: TestClient, actors: Actors) -> None:
    s = bearer(actors.session)
    api.post("/api/v1/users", json={"username": "second", "password": "second-password"}, headers=s)
    login = api.post("/api/v1/auth/login", json={"username": "second", "password": "second-password"})
    second_session = login.json()["token"]
    created = api.post(
        "/api/v1/tokens", json={"name": "t", "scope": "read_only"}, headers=bearer(second_session)
    )
    agent = created.json()["secret"]
    second_id = login.json()["user"]["id"]
    assert api.delete(f"/api/v1/users/{second_id}", headers=s).status_code == 204
    assert api.get("/api/v1/tokens/self", headers=bearer(second_session)).status_code == 401
    assert api.get("/api/v1/tokens/self", headers=bearer(agent)).status_code == 401


def test_change_password(api: TestClient, actors: Actors) -> None:
    s = bearer(actors.session)
    other = api.post("/api/v1/auth/login", json={"username": ADMIN[0], "password": ADMIN[1]}).json()["token"]
    resp = api.post(
        "/api/v1/users/self/password", json={"current": "wrong", "new": "new-password-1"}, headers=s
    )
    assert resp.status_code == 403
    assert _code(resp) is ErrorCode.WRONG_PASSWORD
    assert (
        api.post(
            "/api/v1/users/self/password", json={"current": ADMIN[1], "new": "new-password-1"}, headers=s
        ).status_code
        == 204
    )
    # the calling session survives; the user's other sessions are signed out; agent tokens are untouched
    assert api.get("/api/v1/tokens/self", headers=s).status_code == 200
    assert api.get("/api/v1/tokens/self", headers=bearer(other)).status_code == 401
    assert api.get("/api/v1/tokens/self", headers=bearer(actors.read_only)).status_code == 200
    assert (
        api.post("/api/v1/auth/login", json={"username": ADMIN[0], "password": ADMIN[1]}).status_code == 401
    )
    assert (
        api.post("/api/v1/auth/login", json={"username": ADMIN[0], "password": "new-password-1"}).status_code
        == 200
    )
