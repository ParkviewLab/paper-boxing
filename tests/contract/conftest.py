# SPDX-FileCopyrightText: 2026 Gary Frattarola <garyf@parkviewlab.ai>
#
# SPDX-License-Identifier: MIT OR Apache-2.0

"""Fixtures for the REST contract suite.

`api` is a fresh backend per test with one bootstrap account, and `actors`
holds a bearer token of every kind: a session, an agent token per scope, and
a revoked one. The suite is parametrised over the two implementations of
the contract: `fake`, the in-memory backend in `common/`, and `real`, the
backend proper (`paper_boxing.backend.app.create_app` over a temporary data
directory, with the admin bootstrap set and a cheap argon2 profile so that
every test's sign-ins stay fast). The same tests passing against both is
what proves the two identical.

`clock` moves time forward on either implementation; `fake` exposes the
fake's state (its request record) and skips on the real backend.
"""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path

import argon2
import pytest
from fastapi.testclient import TestClient

from paper_boxing.backend.app import create_app
from paper_boxing.backend.clock import Clock
from paper_boxing.backend.config import BackendConfig
from paper_boxing.common.fake_backend import FakeState, create_fake_backend
from paper_boxing.common.scopes import Scope

ADMIN = ("admin", "admin-password")

# argon2id at the cheapest settings the library accepts: the contract is about behaviour, not cost.
FAST_HASHER = argon2.PasswordHasher(time_cost=1, memory_cost=8 * 1024, parallelism=1)


def real_config(data_dir: Path, *, max_upload_mb: int = 1, session_days: int = 14) -> BackendConfig:
    """A backend configuration over `data_dir` with the contract suite's bootstrap account."""
    return BackendConfig(
        host="127.0.0.1",
        port=0,
        data_dir=data_dir,
        public_sites_url="http://127.0.0.1:35841",
        admin_username=ADMIN[0],
        admin_password=ADMIN[1],
        max_upload_mb=max_upload_mb,
        session_days=session_days,
    )


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


@pytest.fixture(params=["fake", "real"])
def api(request: pytest.FixtureRequest, tmp_path: Path) -> Iterator[TestClient]:
    """A fresh backend per test. The bootstrap account is `ADMIN`."""
    if request.param == "fake":
        app = create_fake_backend(admin=ADMIN, max_upload_mb=1, session_days=14)
    else:
        app = create_app(real_config(tmp_path / "data"), clock=Clock(), password_hasher=FAST_HASHER)
    with TestClient(app, base_url="http://localhost") as client:
        yield client


@pytest.fixture
def clock(api: TestClient) -> Clock:
    """The backend's clock, whichever implementation is under test; `advance(seconds)` moves it."""
    state = api.app.state  # type: ignore[attr-defined]
    return state.fake.clock if hasattr(state, "fake") else state.clock


@pytest.fixture
def fake(api: TestClient) -> FakeState:
    """The fake's state, for tests that need its request record (fake only)."""
    state = api.app.state  # type: ignore[attr-defined]
    if not hasattr(state, "fake"):
        pytest.skip("the request record is the fake backend's own; the real one logs instead")
    return state.fake


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
