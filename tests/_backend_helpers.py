# SPDX-FileCopyrightText: 2026 Gary Frattarola <garyf@parkviewlab.ai>
#
# SPDX-License-Identifier: MIT OR Apache-2.0

"""Helpers for the backend's tests: a backend over a temporary directory, a
sign-in, a site. The contract suite's `real` parameter uses the same
configuration and hasher, so the two test tiers exercise one shape of app."""

from __future__ import annotations

from pathlib import Path

import argon2
from fastapi import FastAPI
from fastapi.testclient import TestClient

from paper_boxing.backend.app import create_app
from paper_boxing.backend.clock import Clock
from paper_boxing.backend.config import BackendConfig
from paper_boxing.common.scopes import Scope

ADMIN = ("admin", "admin-password")

# argon2id at the cheapest settings the library accepts: the tests are about behaviour, not cost.
FAST_HASHER = argon2.PasswordHasher(time_cost=1, memory_cost=8 * 1024, parallelism=1)


def backend_config(
    data_dir: Path,
    *,
    admin: tuple[str, str] | None = ADMIN,
    max_upload_mb: int = 1,
    session_days: int = 14,
    public_sites_url: str = "http://127.0.0.1:35841",
) -> BackendConfig:
    """A backend configuration over `data_dir`; `admin` is the bootstrap pair, or None for no account."""
    return BackendConfig(
        host="127.0.0.1",
        port=0,
        data_dir=data_dir,
        public_sites_url=public_sites_url,
        admin_username=None if admin is None else admin[0],
        admin_password=None if admin is None else admin[1],
        max_upload_mb=max_upload_mb,
        session_days=session_days,
    )


def backend_app(
    config: BackendConfig, *, clock: Clock | None = None, hasher: argon2.PasswordHasher | None = FAST_HASHER
) -> FastAPI:
    return create_app(config, clock=clock or Clock(), password_hasher=hasher)


def bearer(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def login(client: TestClient, username: str = ADMIN[0], password: str = ADMIN[1]) -> str:
    resp = client.post("/api/v1/auth/login", json={"username": username, "password": password})
    assert resp.status_code == 200, resp.text
    return resp.json()["token"]


def create_site(client: TestClient, session: str, name: str) -> str:
    resp = client.post("/api/v1/sites", json={"name": name}, headers=bearer(session))
    assert resp.status_code == 201, resp.text
    return resp.json()["slug"]


def agent_token(client: TestClient, session: str, name: str, scope: Scope) -> tuple[str, str]:
    """An agent token created by the session's user; returns (id, secret)."""
    resp = client.post("/api/v1/tokens", json={"name": name, "scope": scope.value}, headers=bearer(session))
    assert resp.status_code == 201, resp.text
    return resp.json()["token"]["id"], resp.json()["secret"]
