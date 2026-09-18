# SPDX-FileCopyrightText: 2026 Gary Frattarola <garyf@parkviewlab.ai>
#
# SPDX-License-Identifier: MIT OR Apache-2.0

"""What the site server answers as Content-Type, proven through the real
nginx: one site holding a file of every extension in
`tests/_site_server_types.py` (each text extension, the bare dot-files,
names with no extension, one of them inside a folder whose own name has a
dot, extensions in no table, the two script types, the manifest, and a
sample of the stock page, data, image, font and binary types), each fetched
through nginx byte for byte with the exact Content-Type header: `text/plain;
charset=utf-8` for text and for a name with no extension, the stock
`application/octet-stream` for an extension in no table, the bare type for
everything else. Then the parts that must not change: a folder asked for
without its slash still redirects to it and is listed, the listing of a
folder without an index and the 404 page keep their type with no charset,
and the server stays read-only.

The site is created through the REST API and deleted at the end, so the
tier leaves the stack as it found it.
"""

from __future__ import annotations

import random
import secrets
from collections.abc import Iterator

import httpx
import pytest

from tests._site_server_types import (
    ADDED_JAVASCRIPT,
    ADDED_MANIFEST,
    DOT_FILE_EXTENSIONS,
    EXTENSIONLESS_NAMES,
    NO_TABLE_EXTENSIONS,
    SLASHLESS_FOLDERS,
    STOCK_DEFAULT_TYPE,
    STOCK_UNCHANGED,
    TEXT_PLAIN_EXTENSIONS,
    expected_content_type,
)
from tests.integration._tree import TreeFile, png
from tests.integration.conftest import (
    SITES,
    TIMEOUT,
    Deployed,
    Rest,
    assert_served_as_uploaded,
    fetch,
)

pytestmark = pytest.mark.integration

TEXT_SAMPLE_TYPES = {"html", "htm", "css", "js", "svg", "json", "xml"}


def _text(name: str) -> bytes:
    """A short UTF-8 sample beyond ASCII, so the charset the header claims is the one the bytes are in."""
    return f"{name}: served as text, unchanged: über — 日本語\n".encode()


def type_tree() -> tuple[TreeFile, ...]:
    """One file per extension of the table, plus the extensionless names and the extensions in no table."""
    rng = random.Random("site server types")
    files: list[TreeFile] = []
    for ext in TEXT_PLAIN_EXTENSIONS:
        path = f".{ext}" if ext in DOT_FILE_EXTENSIONS else f"text/sample.{ext}"
        files.append(TreeFile(path, _text(path), "text/plain"))
    for name in EXTENSIONLESS_NAMES:
        files.append(TreeFile(name, _text(name), "text/plain"))
    for ext in NO_TABLE_EXTENSIONS:
        # Binary bytes, as the real thing would be: the point is that they download rather than display.
        path = f"other/bundle.js.{ext}" if ext == "map" else f"other/sample.{ext}"
        files.append(TreeFile(path, b"\x1f\x8b\x00\x00" + rng.randbytes(64), STOCK_DEFAULT_TYPE))
    for ext in ADDED_JAVASCRIPT:
        files.append(TreeFile(f"js/module.{ext}", b"export const answer = 42;\n", "application/javascript"))
    for ext, media_type in ADDED_MANIFEST.items():
        files.append(TreeFile(f"app.{ext}", b'{"name": "types", "start_url": "./"}\n', media_type))
    for ext, media_type in STOCK_UNCHANGED.items():
        if ext == "png":
            data = png(8, 8, rng)
        elif ext in TEXT_SAMPLE_TYPES:
            data = _text(ext)
        else:
            data = b"\x00\x01\x02\x03" + rng.randbytes(64)
        files.append(TreeFile(f"stock/sample.{ext}", data, media_type))
    assert len({file.path for file in files}) == len(files)
    return tuple(files)


TYPE_TREE = type_tree()


@pytest.fixture(scope="module")
def types_site(admin: Rest) -> Iterator[Deployed]:
    """A site holding one file of every kind, created and filled through the REST API, deleted at the end."""
    suffix = secrets.token_hex(3)
    site = admin.create_site(f"Integration types {suffix}")
    slug = site["slug"]
    try:
        for file in TYPE_TREE:
            result = admin.upload(slug, file.path, file.data)
            assert result["sha256"] == file.sha256, file.path
        yield Deployed(slug=slug, tree=TYPE_TREE)
    finally:
        admin.delete_site_if_present(slug)


@pytest.mark.parametrize("file", TYPE_TREE, ids=[file.path for file in TYPE_TREE])
def test_every_kind_of_file_is_served_with_its_exact_content_type(
    types_site: Deployed, file: TreeFile
) -> None:
    """The bytes unchanged, the type of the table, the charset on `text/plain` and on nothing else."""
    assert_served_as_uploaded(types_site.slug, file)
    response = fetch(types_site.slug, file.path)
    assert response.headers["content-type"] == expected_content_type(file.media_type), file.path


def test_the_site_holds_every_file_of_the_table(admin: Rest, types_site: Deployed) -> None:
    site = admin.get_site(types_site.slug).json()
    assert site["file_count"] == len(TYPE_TREE)
    assert site["bytes"] == sum(len(file.data) for file in TYPE_TREE)


@pytest.mark.parametrize("folder", SLASHLESS_FOLDERS)
def test_a_folder_asked_for_without_its_slash_still_redirects_and_is_listed(
    types_site: Deployed, folder: str
) -> None:
    """A folder's name matches the extensionless location when it has no dot; the redirect and the listing are the same either way."""
    redirect = fetch(types_site.slug, folder)
    assert redirect.status_code == 301, folder
    assert redirect.headers["location"] == f"/{types_site.slug}/{folder}/"
    listing = fetch(types_site.slug, f"{folder}/")
    assert listing.status_code == 200
    assert listing.headers["content-type"] == "text/html"
    assert listing.headers["cache-control"] == "no-cache"
    entries = [
        file.path.removeprefix(f"{folder}/") for file in TYPE_TREE if file.path.startswith(f"{folder}/")
    ]
    assert entries
    for entry in entries:
        assert f'href="{entry}"' in listing.text, entry
    missing = fetch(types_site.slug, f"{folder}/nowhere")
    assert missing.status_code == 404
    assert missing.headers["content-type"] == "text/html"


def test_the_listing_and_the_error_page_keep_their_type_without_a_charset(types_site: Deployed) -> None:
    """The charset lives in the text type alone, so nginx's own pages (the listing and the 404 page), which are HTML, gain no charset parameter."""
    listing = fetch(types_site.slug, "text/")
    assert listing.status_code == 200
    assert listing.headers["content-type"] == "text/html"
    assert listing.headers["cache-control"] == "no-cache"
    assert "sample.py" in listing.text
    root = fetch(types_site.slug)
    assert root.status_code == 200
    assert root.headers["content-type"] == "text/html"
    for name in ("README", "Makefile", "text/", "v1.2/", "stock/"):
        assert f'href="{name}"' in root.text, name
    assert ".gitignore" not in root.text  # autoindex omits dot-files; the file itself is served above
    missing = fetch(types_site.slug, "text/nowhere.py")
    assert missing.status_code == 404
    assert missing.headers["content-type"] == "text/html"


def test_the_server_stays_read_only(types_site: Deployed) -> None:
    """A write through nginx is refused and the file is as uploaded afterwards."""
    for path in ("text/sample.py", "README"):
        file = types_site.file(path)
        url = f"{SITES}/{types_site.slug}/{file.path}"
        assert httpx.put(url, content=b"changed\n", timeout=TIMEOUT).status_code == 405, path
        assert httpx.delete(url, timeout=TIMEOUT).status_code == 405, path
        assert fetch(types_site.slug, file.path).content == file.data, path
