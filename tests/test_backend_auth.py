# SPDX-FileCopyrightText: 2026 Gary Frattarola <garyf@parkviewlab.ai>
#
# SPDX-License-Identifier: MIT OR Apache-2.0

"""Accounts, sessions and tokens on the real backend: nothing stored in plain
text, the token format, sliding expiry under a clock, `GET /tokens/self` for
live, revoked and expired tokens, the re-hash at sign-in, the admin bootstrap,
the last-user guard, the password change, the logging of who did what, and
the error body on the framework's own errors."""

from __future__ import annotations

import hashlib
import logging
import re
import sqlite3
from dataclasses import replace
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

import argon2
import pytest
from fastapi.routing import APIRoute
from fastapi.testclient import TestClient

from paper_boxing.backend.accounts import new_secret, parse_bearer, secret_digest
from paper_boxing.backend.clock import Clock
from paper_boxing.backend.config import BackendConfig
from paper_boxing.backend.storage import Storage
from paper_boxing.common.routes import ROUTES
from paper_boxing.common.schema import ErrorBody, ErrorCode, TokenSelf
from paper_boxing.common.scopes import Scope
from tests._backend_helpers import (
    ADMIN,
    agent_token,
    backend_app,
    backend_config,
    bearer,
    create_site,
    login,
)

DAY = 24 * 3600
SECRET_RE = re.compile(r"^pb_[A-Za-z0-9_-]{43}$")


def _code(resp: Any) -> ErrorCode:
    return ErrorBody.model_validate(resp.json()).error.code


def _db_text(config: BackendConfig) -> str:
    """Every value in every table, as text, plus the raw bytes of the database files."""
    conn = sqlite3.connect(config.database_path)
    values = [
        str(value)
        for table in ("users", "tokens", "sites")
        for row in conn.execute(f"SELECT * FROM {table}")
        for value in row
    ]
    conn.close()
    raw = b"".join(
        p.read_bytes() for p in (config.database_path, Path(f"{config.database_path}-wal")) if p.exists()
    )
    return "\n".join(values) + "\n" + raw.decode("latin-1")


def _column(config: BackendConfig, query: str) -> list[str]:
    conn = sqlite3.connect(config.database_path)
    try:
        return [row[0] for row in conn.execute(query)]
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Secrets at rest


def test_passwords_and_secrets_are_never_stored_in_plain_text(tmp_path: Path) -> None:
    config = backend_config(tmp_path / "data")
    with TestClient(backend_app(config)) as client:
        session = login(client)
        _, agent = agent_token(client, session, "agent", Scope.READ_ONLY)
        resp = client.post(
            "/api/v1/users",
            json={"username": "second", "password": "second-password"},
            headers=bearer(session),
        )
        assert resp.status_code == 201
    text = _db_text(config)
    for plain in (ADMIN[1], "second-password", session, agent, session[3:], agent[3:]):
        assert plain not in text
    hashes = _column(config, "SELECT password_hash FROM users")
    assert len(hashes) == 2
    assert all(h.startswith("$argon2id$v=19$") for h in hashes)
    assert len({h.split("$")[4] for h in hashes}) == 2  # a different salt per hash
    digests = set(_column(config, "SELECT secret_sha256 FROM tokens"))
    assert {
        hashlib.sha256(session.encode()).hexdigest(),
        hashlib.sha256(agent.encode()).hexdigest(),
    } <= digests


def test_token_format(tmp_path: Path) -> None:
    with TestClient(backend_app(backend_config(tmp_path / "data"))) as client:
        session = login(client)
        _, agent = agent_token(client, session, "agent", Scope.READ_WRITE)
    assert SECRET_RE.match(session) and SECRET_RE.match(agent)
    assert session != agent
    generated = {new_secret() for _ in range(64)}
    assert len(generated) == 64 and all(SECRET_RE.match(s) for s in generated)
    assert secret_digest("pb_x") == hashlib.sha256(b"pb_x").hexdigest()


def test_parse_bearer() -> None:
    assert parse_bearer("Bearer pb_abc") == "pb_abc"
    assert parse_bearer("bearer  pb_abc ") == "pb_abc"
    for bad in (None, "", "Bearer", "Bearer ", "Basic pb_abc", "pb_abc"):
        assert parse_bearer(bad) is None, bad


# ---------------------------------------------------------------------------
# Sessions and tokens under a clock


def _me(client: TestClient, token: str) -> Any:
    return client.get("/api/v1/tokens/self", headers=bearer(token))


def test_session_expiry_slides_with_every_request(tmp_path: Path) -> None:
    clock = Clock()
    with TestClient(backend_app(backend_config(tmp_path / "data", session_days=14), clock=clock)) as client:
        resp = client.post("/api/v1/auth/login", json={"username": ADMIN[0], "password": ADMIN[1]})
        session = resp.json()["token"]
        issued = datetime.fromisoformat(resp.json()["expires_at"])
        assert abs((issued - clock.now()) - timedelta(days=14)) < timedelta(seconds=5)

        clock.advance(13 * DAY)
        me = _me(client, session)
        assert me.status_code == 200
        extended = TokenSelf.model_validate(me.json()).expires_at
        assert extended is not None
        assert abs((extended - issued) - timedelta(days=13)) < timedelta(seconds=5)

        clock.advance(13 * DAY)
        assert _me(client, session).status_code == 200  # 26 days after sign-in, still valid: it slid

        clock.advance(14 * DAY + 1)
        resp = _me(client, session)
        assert resp.status_code == 401
        assert _code(resp) is ErrorCode.UNAUTHORIZED
        clock.advance(-10 * DAY)  # expiry is final: the session was ended when it was found expired
        assert _me(client, session).status_code == 401


def test_tokens_self_for_live_revoked_and_expired_tokens(tmp_path: Path) -> None:
    clock = Clock()
    with TestClient(backend_app(backend_config(tmp_path / "data"), clock=clock)) as client:
        session = login(client)
        other = login(client)
        agent_id, agent = agent_token(client, session, "agent", Scope.READ_WRITE)

        me = TokenSelf.model_validate(_me(client, agent).json())
        assert me.id == agent_id and me.type == "agent" and me.scope == "read_write"
        assert me.username == ADMIN[0] and me.expires_at is None
        me = TokenSelf.model_validate(_me(client, session).json())
        assert me.type == "session" and me.scope == "remove_destructive" and me.expires_at is not None

        assert client.delete(f"/api/v1/tokens/{agent_id}", headers=bearer(session)).status_code == 204
        assert _me(client, agent).status_code == 401
        assert client.post("/api/v1/auth/logout", headers=bearer(other)).status_code == 204
        assert _me(client, other).status_code == 401
        clock.advance(400 * DAY)
        assert _me(client, session).status_code == 401
        for unknown in (new_secret(), "pb_", "not-a-token"):
            assert _me(client, unknown).status_code == 401


def test_password_is_rehashed_at_sign_in_when_the_parameters_rose(tmp_path: Path) -> None:
    config = backend_config(tmp_path / "data")
    weak = argon2.PasswordHasher(time_cost=1, memory_cost=8 * 1024, parallelism=1)
    strong = argon2.PasswordHasher(time_cost=2, memory_cost=16 * 1024, parallelism=1)
    with TestClient(backend_app(config, hasher=weak)) as client:
        login(client)
    (before,) = _column(config, "SELECT password_hash FROM users")
    assert "$m=8192,t=1,p=1$" in before

    with TestClient(backend_app(config, hasher=strong)) as client:
        login(client)
    (after,) = _column(config, "SELECT password_hash FROM users")
    assert after != before
    assert "$m=16384,t=2,p=1$" in after

    with TestClient(backend_app(config, hasher=strong)) as client:
        login(client)
        assert (
            client.post("/api/v1/auth/login", json={"username": ADMIN[0], "password": "wrong"}).status_code
            == 401
        )
    assert _column(config, "SELECT password_hash FROM users") == [after]  # no needless re-hash


def test_default_hasher_is_argon2id(tmp_path: Path) -> None:
    config = backend_config(tmp_path / "data")
    with TestClient(backend_app(config, hasher=None)) as client:
        login(client)
    (stored,) = _column(config, "SELECT password_hash FROM users")
    assert stored.startswith("$argon2id$")
    assert argon2.PasswordHasher().verify(stored, ADMIN[1])


# ---------------------------------------------------------------------------
# The admin bootstrap and the accounts


def test_admin_bootstrap_creates_one_account_once_and_never_again(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    config = backend_config(tmp_path / "data")
    with TestClient(backend_app(config)) as client:
        session = login(client)
        assert [
            u["username"] for u in client.get("/api/v1/users", headers=bearer(session)).json()["users"]
        ] == ["admin"]

    other = replace(config, admin_username="other", admin_password="other-password")
    with TestClient(backend_app(other)) as client:
        session = login(client)  # the original account, unchanged
        assert (
            client.post(
                "/api/v1/auth/login", json={"username": "other", "password": "other-password"}
            ).status_code
            == 401
        )
        assert [
            u["username"] for u in client.get("/api/v1/users", headers=bearer(session)).json()["users"]
        ] == ["admin"]
    with TestClient(backend_app(other)) as client:
        login(client)
    assert _column(config, "SELECT username FROM users") == ["admin"]

    empty = backend_config(tmp_path / "empty", admin=None)
    with (
        caplog.at_level(logging.WARNING, logger="paper_boxing.backend"),
        TestClient(backend_app(empty)) as client,
    ):
        resp = client.post("/api/v1/auth/login", json={"username": ADMIN[0], "password": ADMIN[1]})
        assert resp.status_code == 401
    assert "PAPER_BOXING_ADMIN_USERNAME" in caplog.text
    assert _column(empty, "SELECT username FROM users") == []


def test_an_invalid_admin_pair_stops_the_service_from_starting(tmp_path: Path) -> None:
    short = backend_config(tmp_path / "data", admin=("admin", "short"))
    with pytest.raises(ValueError, match="PAPER_BOXING_ADMIN_PASSWORD"), TestClient(backend_app(short)):
        pass
    bad_name = backend_config(tmp_path / "data2", admin=("bad name", "long-enough"))
    with pytest.raises(ValueError, match="PAPER_BOXING_ADMIN_USERNAME"), TestClient(backend_app(bad_name)):
        pass


def test_last_user_guard(tmp_path: Path) -> None:
    with TestClient(backend_app(backend_config(tmp_path / "data"))) as client:
        session = login(client)
        s = bearer(session)
        admin_id = client.get("/api/v1/users", headers=s).json()["users"][0]["id"]
        resp = client.delete(f"/api/v1/users/{admin_id}", headers=s)
        assert resp.status_code == 409 and _code(resp) is ErrorCode.LAST_USER
        second = client.post(
            "/api/v1/users", json={"username": "second", "password": "second-password"}, headers=s
        ).json()
        assert (
            client.delete(f"/api/v1/users/{admin_id}", headers=s).status_code == 204
        )  # the admin removes itself
        assert _me(client, session).status_code == 401  # and its session died with it
        second_session = login(client, "second", "second-password")
        resp = client.delete(f"/api/v1/users/{second['id']}", headers=bearer(second_session))
        assert resp.status_code == 409 and _code(resp) is ErrorCode.LAST_USER
        assert client.delete("/api/v1/users/usr_nowhere", headers=bearer(second_session)).status_code == 404


def test_password_change_signs_out_the_other_sessions_only(tmp_path: Path) -> None:
    config = backend_config(tmp_path / "data")
    with TestClient(backend_app(config)) as client:
        mine = login(client)
        other = login(client)
        third = login(client)
        _, agent = agent_token(client, mine, "agent", Scope.READ_ONLY)
        resp = client.post(
            "/api/v1/users/self/password",
            json={"current": "wrong", "new": "new-password-1"},
            headers=bearer(mine),
        )
        assert resp.status_code == 403 and _code(resp) is ErrorCode.WRONG_PASSWORD
        assert _me(client, other).status_code == 200
        resp = client.post(
            "/api/v1/users/self/password",
            json={"current": ADMIN[1], "new": "new-password-1"},
            headers=bearer(mine),
        )
        assert resp.status_code == 204
        assert _me(client, mine).status_code == 200
        assert _me(client, other).status_code == 401
        assert _me(client, third).status_code == 401
        assert _me(client, agent).status_code == 200
        assert (
            client.post("/api/v1/auth/login", json={"username": ADMIN[0], "password": ADMIN[1]}).status_code
            == 401
        )
        assert login(client, ADMIN[0], "new-password-1")
    assert "new-password-1" not in _db_text(config)


def test_removing_a_user_removes_their_tokens_from_the_database(tmp_path: Path) -> None:
    config = backend_config(tmp_path / "data")
    with TestClient(backend_app(config)) as client:
        admin = login(client)
        user = client.post(
            "/api/v1/users", json={"username": "second", "password": "second-password"}, headers=bearer(admin)
        ).json()
        second = login(client, "second", "second-password")
        agent_id, agent = agent_token(client, second, "agent", Scope.REMOVE_DESTRUCTIVE)
        assert agent_id in _column(config, "SELECT id FROM tokens")
        assert client.delete(f"/api/v1/users/{user['id']}", headers=bearer(admin)).status_code == 204
        assert _me(client, second).status_code == 401
        assert _me(client, agent).status_code == 401
        assert client.delete(f"/api/v1/tokens/{agent_id}", headers=bearer(admin)).status_code == 404
    assert agent_id not in _column(config, "SELECT id FROM tokens")
    assert user["id"] not in _column(config, "SELECT id FROM users")


# ---------------------------------------------------------------------------
# Logging: who did what, never a secret


def test_requests_are_logged_under_the_acting_token_with_the_via_header(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    with TestClient(backend_app(backend_config(tmp_path / "data"))) as client:
        session = login(client)
        agent_id, agent = agent_token(client, session, "claude", Scope.READ_WRITE)
        with caplog.at_level(logging.INFO, logger="paper_boxing.backend"):
            client.get("/api/v1/sites", headers={**bearer(agent), "X-Paper-Boxing-Via": "mcp"})
            client.post(
                "/api/v1/sites",
                json={"name": "Logged"},
                headers={**bearer(agent), "X-Paper-Boxing-Via": "mcp"},
            )
            client.get("/api/v1/sites", headers=bearer(session))
            client.post("/api/v1/auth/login", json={"username": ADMIN[0], "password": "hunter2-secret"})
    lines = [r.getMessage() for r in caplog.records if r.name.startswith("paper_boxing.backend")]
    assert any(f"route=list_sites user=admin token={agent_id} type=agent via=mcp" in line for line in lines)
    assert any(
        f"create_site user=admin token={agent_id} type=agent via=mcp site=logged" in line for line in lines
    )
    assert any(
        "route=list_sites user=admin token=tok_" in line and "type=session" in line and "via=" not in line
        for line in lines
    )
    assert any("login failed username=admin" in line for line in lines)
    for secret in (session, agent, session[3:], agent[3:], ADMIN[1], "hunter2-secret"):
        assert secret not in caplog.text


# ---------------------------------------------------------------------------
# The framework's own errors, and the route table


def test_framework_errors_use_the_contract_error_body(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    app = backend_app(backend_config(tmp_path / "data"))
    with TestClient(app, raise_server_exceptions=False) as client:
        s = bearer(login(client))
        slug = create_site(client, s["Authorization"].split()[1], "Errors")
        cases = [
            (client.get("/api/v1/nowhere", headers=s), 404, ErrorCode.NOT_FOUND),
            (client.patch("/api/v1/sites", headers=s), 405, ErrorCode.METHOD_NOT_ALLOWED),
            (client.post("/api/v1/sites", json={"name": ""}, headers=s), 422, ErrorCode.VALIDATION_ERROR),
            (
                client.post(
                    "/api/v1/sites", content=b"{not json", headers={**s, "Content-Type": "application/json"}
                ),
                422,
                ErrorCode.VALIDATION_ERROR,
            ),
            (
                client.put(
                    f"/api/v1/sites/{slug}/files/a.txt",
                    content=b"x",
                    params={"overwrite": "maybe"},
                    headers=s,
                ),
                422,
                ErrorCode.VALIDATION_ERROR,
            ),
        ]

        def explode(self: Storage, slug: str, folder: str) -> None:
            raise RuntimeError("simulated defect")

        monkeypatch.setattr(Storage, "list_folder", explode)
        cases.append((client.get(f"/api/v1/sites/{slug}/files", headers=s), 500, ErrorCode.INTERNAL_ERROR))

        def vanish(self: Storage, slug: str, folder: str) -> None:
            raise FileNotFoundError(2, "No such file or directory", "/data/sites/gone")

        monkeypatch.setattr(Storage, "list_folder", vanish)  # an OSError that is not a full disk stays 500
        cases.append((client.get(f"/api/v1/sites/{slug}/files", headers=s), 500, ErrorCode.INTERNAL_ERROR))
        for resp, status, code in cases:
            assert resp.status_code == status, resp.text
            body = ErrorBody.model_validate(resp.json())
            assert body.error.code is code and body.error.message


def test_registered_routes_are_exactly_the_table_in_order(tmp_path: Path) -> None:
    app = backend_app(backend_config(tmp_path / "data"))
    registered = [
        (method, r.path)
        for r in app.routes
        if isinstance(r, APIRoute) and r.path.startswith("/api/v1")
        for method in r.methods
    ]
    assert registered == [(r.method, r.path) for r in ROUTES]
    public = {r.path for r in app.routes if isinstance(r, APIRoute) and not r.path.startswith("/api/v1")}
    assert public == {"/health", "/admin/version"}


def test_docs_and_openapi_are_served(tmp_path: Path) -> None:
    with TestClient(backend_app(backend_config(tmp_path / "data"))) as client:
        assert client.get("/docs").status_code == 200
        spec = client.get("/openapi.json").json()
        assert "/api/v1/sites/{slug}/files/{path}" in spec["paths"]
