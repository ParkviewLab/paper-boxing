# SPDX-FileCopyrightText: 2026 Gary Frattarola <garyf@parkviewlab.ai>
#
# SPDX-License-Identifier: MIT OR Apache-2.0

"""The naming rules of the contract: slugs and site paths."""

from __future__ import annotations

import pytest

from paper_boxing.common.naming import (
    path_name,
    path_parent,
    slug_from_name,
    validate_site_path,
    validate_slug,
)


@pytest.mark.parametrize(
    ("name", "slug"),
    [
        ("Paper Boxing", "paper-boxing"),
        ("  PensaForma: design page!  ", "pensaforma-design-page"),
        ("Café Ünïcode", "cafe-unicode"),
        ("already-a-slug", "already-a-slug"),
        ("--leading and trailing--", "leading-and-trailing"),
        ("multiple   spaces___and.dots", "multiple-spaces-and-dots"),
        ("x" * 100, "x" * 63),
        ("42", "42"),
    ],
)
def test_slug_from_name(name: str, slug: str) -> None:
    assert slug_from_name(name) == slug
    assert validate_slug(slug) == slug


@pytest.mark.parametrize("name", ["", "   ", "!!!", "日本語", "---"])
def test_slug_from_name_rejects_names_with_nothing_usable(name: str) -> None:
    with pytest.raises(ValueError):
        slug_from_name(name)


@pytest.mark.parametrize("slug", ["", "-a", "a-", "A", "a b", "a/b", "a" * 64, "a..b"])
def test_validate_slug_rejects(slug: str) -> None:
    with pytest.raises(ValueError):
        validate_slug(slug)


@pytest.mark.parametrize(
    ("raw", "canonical"),
    [
        ("index.html", "index.html"),
        ("/index.html", "index.html"),
        ("docs/a/b.css", "docs/a/b.css"),
        ("docs/a/", "docs/a"),
        ("///docs", "docs"),
        (".well-known/x", ".well-known/x"),
        ("naïve name.txt", "naïve name.txt"),
    ],
)
def test_validate_site_path_canonical_form(raw: str, canonical: str) -> None:
    assert validate_site_path(raw) == canonical


@pytest.mark.parametrize(
    "raw",
    [
        "../etc/passwd",
        "docs/../../x",
        "docs/./x",
        ".",
        "..",
        "a//b",
        "a\\b",
        "a\x00b",
        "a\nb",
        "x" * 1025,
        "a/" + "y" * 256,
    ],
)
def test_validate_site_path_rejects(raw: str) -> None:
    with pytest.raises(ValueError):
        validate_site_path(raw)


def test_root_only_when_allowed() -> None:
    assert validate_site_path("", allow_root=True) == ""
    assert validate_site_path("/", allow_root=True) == ""
    with pytest.raises(ValueError):
        validate_site_path("")
    with pytest.raises(ValueError):
        validate_site_path("/")


def test_parent_and_name() -> None:
    assert path_parent("a/b/c.txt") == "a/b"
    assert path_parent("c.txt") == ""
    assert path_name("a/b/c.txt") == "c.txt"
    assert path_name("c.txt") == "c.txt"
