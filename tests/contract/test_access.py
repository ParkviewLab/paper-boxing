# SPDX-FileCopyrightText: 2026 Gary Frattarola <garyf@parkviewlab.ai>
#
# SPDX-License-Identifier: MIT OR Apache-2.0

"""Every route against every kind of caller: the access matrix of docs/api.md."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

import httpx
import pytest
from fastapi.testclient import TestClient

from paper_boxing.common.routes import ROUTES, Access, RouteSpec, build_path
from paper_boxing.common.scopes import Scope
from tests.contract.conftest import ADMIN, Actors, bearer

Call = Callable[[TestClient, dict[str, str]], httpx.Response]


@dataclass(frozen=True)
class Fixture:
    slug: str
    user_id: str


def _prepare(api: TestClient, actors: Actors) -> Fixture:
    """A site with a file and a folder, and a second user, so every authorized call has a target."""
    s = bearer(actors.session)
    site = api.post("/api/v1/sites", json={"name": "Target"}, headers=s)
    assert site.status_code == 201, site.text
    slug = site.json()["slug"]
    assert (
        api.put(f"/api/v1/sites/{slug}/files/docs/index.html", content=b"<p>x</p>", headers=s).status_code
        == 201
    )
    assert api.put(f"/api/v1/sites/{slug}/files/empty/.keep", content=b"", headers=s).status_code == 201
    assert api.delete(f"/api/v1/sites/{slug}/files/empty/.keep", headers=s).status_code == 204
    user = api.post("/api/v1/users", json={"username": "second", "password": "second-password"}, headers=s)
    assert user.status_code == 201, user.text
    return Fixture(slug=slug, user_id=user.json()["id"])


def _calls(f: Fixture, actors: Actors) -> dict[str, Call]:
    """One well-formed request per route, so an authorized caller never fails for another reason."""
    slug = f.slug
    return {
        "login": lambda c, h: c.post(
            build_path("login"), json={"username": ADMIN[0], "password": ADMIN[1]}, headers=h
        ),
        "logout": lambda c, h: c.post(build_path("logout"), headers=h),
        "list_sites": lambda c, h: c.get(build_path("list_sites"), headers=h),
        "create_site": lambda c, h: c.post(build_path("create_site"), json={"name": "Another"}, headers=h),
        "get_site": lambda c, h: c.get(build_path("get_site", slug=slug), headers=h),
        "delete_site": lambda c, h: c.delete(
            build_path("delete_site", slug=slug), params={"confirm": slug}, headers=h
        ),
        "list_files": lambda c, h: c.get(build_path("list_files", slug=slug), headers=h),
        "upload_file": lambda c, h: c.put(
            build_path("upload_file", slug=slug, path="new.txt"), content=b"n", headers=h
        ),
        "download_file": lambda c, h: c.get(
            build_path("download_file", slug=slug, path="docs/index.html"), headers=h
        ),
        "delete_file": lambda c, h: c.delete(
            build_path("delete_file", slug=slug, path="docs/index.html"), headers=h
        ),
        "delete_folder": lambda c, h: c.delete(
            build_path("delete_folder", slug=slug, path="empty"), headers=h
        ),
        "list_tokens": lambda c, h: c.get(build_path("list_tokens"), headers=h),
        "create_token": lambda c, h: c.post(
            build_path("create_token"), json={"name": "t", "scope": "read_only"}, headers=h
        ),
        "token_self": lambda c, h: c.get(build_path("token_self"), headers=h),
        "revoke_token": lambda c, h: c.delete(
            build_path("revoke_token", token_id=actors.read_only_id), headers=h
        ),
        "list_users": lambda c, h: c.get(build_path("list_users"), headers=h),
        "create_user": lambda c, h: c.post(
            build_path("create_user"), json={"username": "third", "password": "third-password"}, headers=h
        ),
        "change_password": lambda c, h: c.post(
            build_path("change_password"), json={"current": ADMIN[1], "new": "new-password-1"}, headers=h
        ),
        "delete_user": lambda c, h: c.delete(build_path("delete_user", user_id=f.user_id), headers=h),
    }


CALLERS = ("none", "read_only", "read_write", "remove_destructive", "session")


def _expected(spec: RouteSpec, caller: str) -> int | None:
    """The status an unauthorized caller gets, or None when the caller is authorized."""
    if spec.access is Access.PUBLIC:
        return None
    if caller == "none":
        return 401
    if spec.access is Access.ANY_TOKEN:
        return None
    if spec.access is Access.SESSION:
        return None if caller == "session" else 403
    if caller == "session":
        return None
    return (
        None
        if Scope(caller) >= Scope(spec.access.value) or _tier(caller) >= _tier(spec.access.value)
        else 403
    )


def _tier(scope: str) -> int:
    return ["read_only", "read_write", "remove_destructive"].index(scope)


@pytest.mark.parametrize("caller", CALLERS)
@pytest.mark.parametrize("spec", ROUTES, ids=lambda r: r.name)
def test_access_matrix(api: TestClient, actors: Actors, spec: RouteSpec, caller: str) -> None:
    fixture = _prepare(api, actors)
    calls = _calls(fixture, actors)
    assert set(calls) == {r.name for r in ROUTES}, "every route needs a call in this matrix"
    headers = (
        {}
        if caller == "none"
        else bearer(actors.session if caller == "session" else actors.for_scope(Scope(caller)))
    )
    resp = calls[spec.name](api, headers)
    expected = _expected(spec, caller)
    if expected is None:
        assert resp.status_code not in (401, 403), (spec.name, caller, resp.status_code, resp.text)
        assert resp.status_code < 400, (spec.name, caller, resp.status_code, resp.text)
    else:
        assert resp.status_code == expected, (spec.name, caller, resp.status_code, resp.text)
