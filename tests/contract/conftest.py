# SPDX-FileCopyrightText: 2026 Gary Frattarola <garyf@parkviewlab.ai>
#
# SPDX-License-Identifier: MIT OR Apache-2.0

"""Fixtures for the REST contract suite.

`api` is a fresh backend per test with one bootstrap account, and `actors`
holds a bearer token of every kind: a session, an agent token per scope, and
a revoked one. The suite runs against the fake backend here; the backend
worker adds the real app as a second `api` parameter (a `TestClient` over
`paper_boxing.backend.app` with a temporary data directory and the admin
bootstrap variables set) and the same tests then prove the two identical.
"""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass

import pytest
from fastapi.testclient import TestClient

from paper_boxing.common.fake_backend import FakeState, create_fake_backend
from paper_boxing.common.scopes import Scope

ADMIN = ("admin", "admin-password")


@dataclass(frozen=True)
class Actors:
    """Bearer secrets, one per kind of caller."""

    session: str
    read_only: str
    read_write: str
    remove_destructive: str
    revoked: str
    read_only_id: str
    remove_destructive_id: str

    def for_scope(self, scope: Scope) -> str:
        return {
            Scope.READ_ONLY: self.read_only,
            Scope.READ_WRITE: self.read_write,
            Scope.REMOVE_DESTRUCTIVE: self.remove_destructive,
        }[scope]


@pytest.fixture(params=["fake"])
def api(request: pytest.FixtureRequest) -> Iterator[TestClient]:
    """A fresh backend per test. The bootstrap account is `ADMIN`."""
    app = create_fake_backend(admin=ADMIN, max_upload_mb=1, session_days=14)
    with TestClient(app, base_url="http://localhost") as client:
        yield client


@pytest.fixture
def fake(api: TestClient) -> FakeState:
    """The fake's state, for tests that need the clock or the request record (fake only)."""
    return api.app.state.fake  # type: ignore[attr-defined]


@pytest.fixture
def actors(api: TestClient) -> Actors:
    login = api.post("/api/v1/auth/login", json={"username": ADMIN[0], "password": ADMIN[1]})
    assert login.status_code == 200, login.text
    session = login.json()["token"]

    def agent(name: str, scope: Scope) -> tuple[str, str]:
        resp = api.post(
            "/api/v1/tokens",
            json={"name": name, "scope": scope.value},
            headers={"Authorization": f"Bearer {session}"},
        )
        assert resp.status_code == 201, resp.text
        return resp.json()["token"]["id"], resp.json()["secret"]

    ro_id, ro = agent("ro", Scope.READ_ONLY)
    _, rw = agent("rw", Scope.READ_WRITE)
    rd_id, rd = agent("rd", Scope.REMOVE_DESTRUCTIVE)
    revoked_id, revoked = agent("gone", Scope.REMOVE_DESTRUCTIVE)
    resp = api.delete(f"/api/v1/tokens/{revoked_id}", headers={"Authorization": f"Bearer {session}"})
    assert resp.status_code == 204, resp.text
    return Actors(
        session=session,
        read_only=ro,
        read_write=rw,
        remove_destructive=rd,
        revoked=revoked,
        read_only_id=ro_id,
        remove_destructive_id=rd_id,
    )


def bearer(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}
