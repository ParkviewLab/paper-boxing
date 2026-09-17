# SPDX-FileCopyrightText: 2026 Gary Frattarola <garyf@parkviewlab.ai>
#
# SPDX-License-Identifier: MIT OR Apache-2.0

"""A site made through the REST API with a signed-in session, as the frontend
makes one: created, a tree uploaded file by file, and every file fetched
through nginx and compared byte for byte and by sha256 with what was uploaded,
with the type nginx must answer. Then the site server at the root (the
`index.html`), at an index-less folder (the listing), at a missing path (404)
and at the slash-less address (a redirect that keeps the published port); a
replaced file showing at once; and the deletions, a file, a folder, the site,
each confirmed gone through nginx and, for the site, on the volume.

The tests run in file order: the deletions come last, and the site fixture's
teardown tolerates a site that the last test has already removed.
"""

from __future__ import annotations

import secrets
from collections.abc import Iterator

import httpx
import pytest

from tests.integration._tree import site_tree
from tests.integration.conftest import (
    PUBLIC_SITES_URL,
    SITES,
    TIMEOUT,
    Deployed,
    Rest,
    assert_nothing_remains_on_the_volume,
    assert_served_as_uploaded,
    fetch,
    media_type,
)

pytestmark = pytest.mark.integration


@pytest.fixture(scope="module")
def rest_site(admin: Rest) -> Iterator[Deployed]:
    """A site with an `index.html`, created and filled through the REST API, deleted at the end."""
    suffix = secrets.token_hex(3)
    name = f"Integration REST {suffix}"
    site = admin.create_site(name)
    slug = site["slug"]
    assert slug == f"integration-rest-{suffix}"
    assert site["url"] == f"{PUBLIC_SITES_URL}/{slug}/"
    assert site["file_count"] == 0 and site["bytes"] == 0
    tree = site_tree(name, with_index=True)
    try:
        for file in tree:
            result = admin.upload(slug, file.path, file.data)
            assert result == {
                "path": file.path,
                "bytes": len(file.data),
                "sha256": file.sha256,
                "replaced": False,
            }
        yield Deployed(slug=slug, tree=tree)
    finally:
        admin.delete_site_if_present(slug)


def test_the_site_records_what_was_uploaded(admin: Rest, rest_site: Deployed) -> None:
    site = admin.get_site(rest_site.slug).json()
    assert site["file_count"] == len(rest_site.tree)
    assert site["bytes"] == sum(len(file.data) for file in rest_site.tree)
    root = admin.list_files(rest_site.slug)
    assert [entry["name"] for entry in root["entries"] if entry["type"] == "folder"] == [
        "css",
        "docs",
        "fonts",
        "img",
        "js",
        "notes",
    ]
    assert [entry["name"] for entry in root["entries"] if entry["type"] == "file"] == [
        "app.webmanifest",
        "index.html",
    ]
    guide = admin.list_files(rest_site.slug, "docs/guide")
    assert guide["path"] == "docs/guide"
    assert guide["entries"][0]["sha256"] == rest_site.file("docs/guide/chapter-1.html").sha256


def test_every_file_is_served_byte_identical_with_its_type(rest_site: Deployed) -> None:
    for file in rest_site.tree:
        assert_served_as_uploaded(rest_site.slug, file)


def test_the_self_contained_page_is_served_unchanged(rest_site: Deployed) -> None:
    page = rest_site.file("docs/design.html")
    assert len(page.data) >= 100 * 1024
    response = fetch(rest_site.slug, page.path)
    assert response.status_code == 200
    assert response.content == page.data
    assert media_type(response) == "text/html"


def test_the_site_root_serves_index_html(rest_site: Deployed) -> None:
    index = rest_site.file("index.html")
    root = fetch(rest_site.slug)
    assert root.status_code == 200
    assert root.content == index.data
    assert media_type(root) == "text/html"


def test_the_slashless_address_redirects_within_the_published_port(rest_site: Deployed) -> None:
    """`absolute_redirect off`: the Location is the path alone, so the browser keeps the host and port 35841."""
    redirect = httpx.get(f"{SITES}/{rest_site.slug}", timeout=TIMEOUT, follow_redirects=False)
    assert redirect.status_code == 301
    assert redirect.headers["location"] == f"/{rest_site.slug}/"
    followed = httpx.get(f"{SITES}/{rest_site.slug}", timeout=TIMEOUT, follow_redirects=True)
    assert followed.status_code == 200
    assert followed.content == rest_site.file("index.html").data


def test_an_index_less_folder_lists_its_files(rest_site: Deployed) -> None:
    css = fetch(rest_site.slug, "css/")
    assert css.status_code == 200
    assert media_type(css) == "text/html"
    assert "site.css" in css.text
    guide = fetch(rest_site.slug, "docs/guide/")
    assert guide.status_code == 200
    assert "chapter-1.html" in guide.text
    notes = fetch(rest_site.slug, "notes/")
    assert notes.status_code == 200
    assert "%C3%BCber%20plan.txt" in notes.text  # the link is percent-encoded
    assert "über plan.txt" in notes.text  # the name is shown as written


def test_a_missing_path_is_404(rest_site: Deployed) -> None:
    for path in ("nowhere.html", "css/nowhere.css", "docs/nowhere/", "docs/guide/chapter-2.html"):
        assert fetch(rest_site.slug, path).status_code == 404, path
    assert fetch("integration-no-such-site").status_code == 404


def test_a_replaced_file_shows_at_once(admin: Rest, rest_site: Deployed) -> None:
    first = b"body { color: #1f2933; }\n"
    second = b"body { color: #b3541e; }\n"
    created = admin.upload(rest_site.slug, "css/theme.css", first)
    assert created["replaced"] is False
    assert fetch(rest_site.slug, "css/theme.css").content == first
    replaced = admin.upload(rest_site.slug, "css/theme.css", second, overwrite=True)
    assert replaced["replaced"] is True
    served = fetch(rest_site.slug, "css/theme.css")
    assert served.content == second
    assert served.headers["cache-control"] == "no-cache"


def test_deleting_a_file_stops_it_being_served(admin: Rest, rest_site: Deployed) -> None:
    assert fetch(rest_site.slug, "js/app.js").status_code == 200
    admin.delete_file(rest_site.slug, "js/app.js")
    assert fetch(rest_site.slug, "js/app.js").status_code == 404
    listing = fetch(rest_site.slug, "js/")
    assert listing.status_code == 200
    assert "app.js" not in listing.text
    assert "module.mjs" in listing.text
    assert [entry["name"] for entry in admin.list_files(rest_site.slug, "js")["entries"]] == ["module.mjs"]


def test_deleting_a_folder_stops_its_files_being_served(admin: Rest, rest_site: Deployed) -> None:
    assert fetch(rest_site.slug, "docs/design.html").status_code == 200
    admin.delete_folder(rest_site.slug, "docs", recursive=True)
    for path in ("docs/design.html", "docs/guide/chapter-1.html", "docs/guide/", "docs/"):
        assert fetch(rest_site.slug, path).status_code == 404, path
    root = admin.list_files(rest_site.slug)
    assert "docs" not in [entry["name"] for entry in root["entries"]]


def test_deleting_the_site_removes_it_from_the_server_and_the_volume(
    admin: Rest, rest_site: Deployed
) -> None:
    assert fetch(rest_site.slug).status_code == 200
    admin.delete_site(rest_site.slug)
    assert fetch(rest_site.slug).status_code == 404
    assert fetch(rest_site.slug, "index.html").status_code == 404
    assert fetch(rest_site.slug, "css/site.css").status_code == 404
    assert admin.get_site(rest_site.slug).status_code == 404
    assert_nothing_remains_on_the_volume(rest_site.slug)
