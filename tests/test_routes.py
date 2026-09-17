# SPDX-FileCopyrightText: 2026 Gary Frattarola <garyf@parkviewlab.ai>
#
# SPDX-License-Identifier: MIT OR Apache-2.0

"""The route table: uniqueness, ordering, URL building and the access rules."""

from __future__ import annotations

from pathlib import Path

import pytest

from paper_boxing.common.routes import API_PREFIX, ROUTES, Access, access_allows, build_path, site_url
from paper_boxing.common.scopes import Scope, TokenType


def test_routes_are_unique() -> None:
    assert len({r.name for r in ROUTES}) == len(ROUTES)
    assert len({(r.method, r.path) for r in ROUTES}) == len(ROUTES)


def test_every_route_is_under_the_api_prefix() -> None:
    assert all(r.path.startswith(f"{API_PREFIX}/") for r in ROUTES)


def test_literal_segments_precede_parameters() -> None:
    names = [r.name for r in ROUTES]
    assert names.index("token_self") < names.index("revoke_token")
    assert names.index("change_password") < names.index("delete_user")


def test_the_design_routes_are_all_present() -> None:
    expected = {
        ("POST", "/api/v1/auth/login"),
        ("POST", "/api/v1/auth/logout"),
        ("GET", "/api/v1/sites"),
        ("POST", "/api/v1/sites"),
        ("DELETE", "/api/v1/sites/{slug}"),
        ("GET", "/api/v1/sites/{slug}/files"),
        ("PUT", "/api/v1/sites/{slug}/files/{path:path}"),
        ("GET", "/api/v1/sites/{slug}/files/{path:path}"),
        ("DELETE", "/api/v1/sites/{slug}/files/{path:path}"),
        ("DELETE", "/api/v1/sites/{slug}/folders/{path:path}"),
        ("GET", "/api/v1/tokens"),
        ("POST", "/api/v1/tokens"),
        ("DELETE", "/api/v1/tokens/{token_id}"),
        ("GET", "/api/v1/tokens/self"),
        ("GET", "/api/v1/users"),
        ("POST", "/api/v1/users"),
        ("DELETE", "/api/v1/users/{user_id}"),
        ("POST", "/api/v1/users/self/password"),
    }
    assert expected <= {(r.method, r.path) for r in ROUTES}


def test_build_path_quotes_parameters() -> None:
    assert build_path("login") == "/api/v1/auth/login"
    assert build_path("get_site", slug="my-site") == "/api/v1/sites/my-site"
    assert (
        build_path("upload_file", slug="s", path="docs/a b/ü.html")
        == "/api/v1/sites/s/files/docs/a%20b/%C3%BC.html"
    )
    assert build_path("revoke_token", token_id="tok/1") == "/api/v1/tokens/tok%2F1"


def test_build_path_unknown_route() -> None:
    with pytest.raises(KeyError):
        build_path("nowhere")


@pytest.mark.parametrize(
    ("access", "token_type", "scope", "allowed"),
    [
        (Access.PUBLIC, None, None, True),
        (Access.ANY_TOKEN, None, None, False),
        (Access.ANY_TOKEN, TokenType.AGENT, Scope.READ_ONLY, True),
        (Access.READ_ONLY, None, None, False),
        (Access.READ_ONLY, TokenType.AGENT, Scope.READ_ONLY, True),
        (Access.READ_WRITE, TokenType.AGENT, Scope.READ_ONLY, False),
        (Access.READ_WRITE, TokenType.AGENT, Scope.READ_WRITE, True),
        (Access.REMOVE_DESTRUCTIVE, TokenType.AGENT, Scope.READ_WRITE, False),
        (Access.REMOVE_DESTRUCTIVE, TokenType.AGENT, Scope.REMOVE_DESTRUCTIVE, True),
        (Access.REMOVE_DESTRUCTIVE, TokenType.SESSION, Scope.REMOVE_DESTRUCTIVE, True),
        (Access.SESSION, TokenType.AGENT, Scope.REMOVE_DESTRUCTIVE, False),
        (Access.SESSION, TokenType.SESSION, Scope.REMOVE_DESTRUCTIVE, True),
    ],
)
def test_access_allows(
    access: Access, token_type: TokenType | None, scope: Scope | None, allowed: bool
) -> None:
    assert access_allows(access, token_type, scope) is allowed


def test_site_url() -> None:
    assert site_url("http://host:35841", "my-site") == "http://host:35841/my-site/"
    assert site_url("http://host:35841/", "my-site") == "http://host:35841/my-site/"


def test_every_route_is_documented_in_api_md() -> None:
    """docs/api.md is the human-readable contract; it must name every route of the table."""
    doc = (Path(__file__).resolve().parent.parent / "docs" / "api.md").read_text(encoding="utf-8")
    for spec in ROUTES:
        path = spec.path.replace("{path:path}", "{path}")
        assert f"`{spec.method} {path}" in doc, f"{spec.method} {path} is missing from docs/api.md"
