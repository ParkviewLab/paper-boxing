# SPDX-FileCopyrightText: 2026 Gary Frattarola <garyf@parkviewlab.ai>
#
# SPDX-License-Identifier: MIT OR Apache-2.0

"""Scope enforcement per route on the real backend, from the route table:
no token is 401 before anything else; an agent token below the route's tier
is 403 `forbidden`; any agent token on a session-only route is 403
`session_required`; the lowest sufficient caller succeeds. (The contract
suite runs the full caller matrix against both implementations; this is
the boundary, route by route, on the real one.)"""

from __future__ import annotations

from collections.abc import Callable, Iterator
from dataclasses import dataclass
from itertools import count
from pathlib import Path

import httpx
import pytest
from fastapi.testclient import TestClient

from paper_boxing.common.routes import ROUTES, Access, RouteSpec, build_path
from paper_boxing.common.schema import ErrorBody, ErrorCode
from paper_boxing.common.scopes import SCOPE_TIER, Scope
from tests._backend_helpers import ADMIN, agent_token, backend_app, backend_config, bearer, login

_counter = count(1)


@dataclass(frozen=True)
class Target:
    slug: str
    user_id: str
    token_id: str


@dataclass(frozen=True)
class Callers:
    session: str
    agents: dict[Scope, str]


@pytest.fixture(scope="module")
def client(tmp_path_factory: pytest.TempPathFactory) -> Iterator[TestClient]:
    app = backend_app(backend_config(tmp_path_factory.mktemp("backend-access") / "data"))
    with TestClient(app, base_url="http://localhost") as client:
        yield client


@pytest.fixture(scope="module")
def callers(client: TestClient) -> Callers:
    session = login(client)
    return Callers(
        session=session,
        agents={scope: agent_token(client, session, scope.value, scope)[1] for scope in Scope},
    )


def _target(client: TestClient, session: str) -> Target:
    """A fresh site with a file and an empty folder, a user, and a token: something for every route to act on."""
    n = next(_counter)
    s = bearer(session)
    site = client.post("/api/v1/sites", json={"name": f"Target {n}"}, headers=s)
    assert site.status_code == 201, site.text
    slug = site.json()["slug"]
    assert (
        client.put(f"/api/v1/sites/{slug}/files/docs/index.html", content=b"<p>", headers=s).status_code
        == 201
    )
    assert client.put(f"/api/v1/sites/{slug}/files/empty/.keep", content=b"", headers=s).status_code == 201
    assert client.delete(f"/api/v1/sites/{slug}/files/empty/.keep", headers=s).status_code == 204
    user = client.post("/api/v1/users", json={"username": f"user{n}", "password": "user-password"}, headers=s)
    assert user.status_code == 201, user.text
    token_id, _ = agent_token(client, session, f"victim{n}", Scope.READ_ONLY)
    return Target(slug=slug, user_id=user.json()["id"], token_id=token_id)


Call = Callable[[TestClient, Target, dict[str, str]], httpx.Response]

CALLS: dict[str, Call] = {
    "login": lambda c, t, h: c.post(
        build_path("login"), json={"username": ADMIN[0], "password": ADMIN[1]}, headers=h
    ),
    "logout": lambda c, t, h: c.post(build_path("logout"), headers=h),
    "list_sites": lambda c, t, h: c.get(build_path("list_sites"), headers=h),
    "create_site": lambda c, t, h: c.post(
        build_path("create_site"), json={"name": f"Made {next(_counter)}"}, headers=h
    ),
    "get_site": lambda c, t, h: c.get(build_path("get_site", slug=t.slug), headers=h),
    "delete_site": lambda c, t, h: c.delete(
        build_path("delete_site", slug=t.slug), params={"confirm": t.slug}, headers=h
    ),
    "list_files": lambda c, t, h: c.get(build_path("list_files", slug=t.slug), headers=h),
    "upload_file": lambda c, t, h: c.put(
        build_path("upload_file", slug=t.slug, path="new.txt"), content=b"n", headers=h
    ),
    "download_file": lambda c, t, h: c.get(
        build_path("download_file", slug=t.slug, path="docs/index.html"), headers=h
    ),
    "delete_file": lambda c, t, h: c.delete(
        build_path("delete_file", slug=t.slug, path="docs/index.html"), headers=h
    ),
    "delete_folder": lambda c, t, h: c.delete(
        build_path("delete_folder", slug=t.slug, path="empty"), headers=h
    ),
    "list_tokens": lambda c, t, h: c.get(build_path("list_tokens"), headers=h),
    "create_token": lambda c, t, h: c.post(
        build_path("create_token"), json={"name": "t", "scope": "read_only"}, headers=h
    ),
    "token_self": lambda c, t, h: c.get(build_path("token_self"), headers=h),
    "revoke_token": lambda c, t, h: c.delete(build_path("revoke_token", token_id=t.token_id), headers=h),
    "list_users": lambda c, t, h: c.get(build_path("list_users"), headers=h),
    "create_user": lambda c, t, h: c.post(
        build_path("create_user"),
        json={"username": f"new{next(_counter)}", "password": "new-password"},
        headers=h,
    ),
    "change_password": lambda c, t, h: c.post(
        build_path("change_password"), json={"current": ADMIN[1], "new": ADMIN[1]}, headers=h
    ),
    "delete_user": lambda c, t, h: c.delete(build_path("delete_user", user_id=t.user_id), headers=h),
}


def _code(resp: httpx.Response) -> ErrorCode:
    return ErrorBody.model_validate(resp.json()).error.code


def test_every_route_has_a_call() -> None:
    assert set(CALLS) == {r.name for r in ROUTES}


@pytest.mark.parametrize("spec", ROUTES, ids=lambda r: r.name)
def test_scope_boundary(client: TestClient, callers: Callers, spec: RouteSpec) -> None:
    call = CALLS[spec.name]
    if spec.access is Access.PUBLIC:
        assert call(client, _target(client, callers.session), {}).status_code == 200
        return

    # No token: 401 before any other check, even where the target does not exist.
    resp = call(client, Target(slug="nowhere", user_id="usr_nowhere", token_id="tok_nowhere"), {})
    assert resp.status_code == 401 and _code(resp) is ErrorCode.UNAUTHORIZED

    if spec.access is Access.SESSION:
        for scope, secret in callers.agents.items():
            resp = call(client, _target(client, callers.session), bearer(secret))
            assert resp.status_code == 403, (scope, resp.text)
            assert _code(resp) is ErrorCode.SESSION_REQUIRED
        # Logout ends the session it is called with, so it gets a session of its own.
        session = login(client) if spec.name == "logout" else callers.session
        resp = call(client, _target(client, callers.session), bearer(session))
        assert resp.status_code < 400, resp.text
        return

    if spec.access is Access.ANY_TOKEN:
        for secret in (*callers.agents.values(), callers.session):
            assert call(client, _target(client, callers.session), bearer(secret)).status_code == 200
        return

    required = Scope(spec.access.value)
    for scope, secret in callers.agents.items():
        resp = call(client, _target(client, callers.session), bearer(secret))
        if SCOPE_TIER[scope] < SCOPE_TIER[required]:
            assert resp.status_code == 403, (scope, resp.text)
            assert _code(resp) is ErrorCode.FORBIDDEN
        else:
            assert resp.status_code < 400, (scope, resp.text)
    resp = call(client, _target(client, callers.session), bearer(callers.session))
    assert resp.status_code < 400, resp.text


def test_a_refused_call_changes_nothing(client: TestClient, callers: Callers, tmp_path: Path) -> None:
    target = _target(client, callers.session)
    ro = bearer(callers.agents[Scope.READ_ONLY])
    rw = bearer(callers.agents[Scope.READ_WRITE])
    assert client.put(f"/api/v1/sites/{target.slug}/files/x.txt", content=b"x", headers=ro).status_code == 403
    assert client.delete(f"/api/v1/sites/{target.slug}/files/docs/index.html", headers=rw).status_code == 403
    assert (
        client.delete(f"/api/v1/sites/{target.slug}", params={"confirm": target.slug}, headers=rw).status_code
        == 403
    )
    listing = client.get(f"/api/v1/sites/{target.slug}/files", headers=ro).json()
    assert [e["name"] for e in listing["entries"]] == ["docs", "empty"]
    assert client.get(f"/api/v1/sites/{target.slug}/files/docs/index.html", headers=ro).content == b"<p>"
