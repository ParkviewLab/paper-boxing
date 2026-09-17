# SPDX-FileCopyrightText: 2026 Gary Frattarola <garyf@parkviewlab.ai>
#
# SPDX-License-Identifier: MIT OR Apache-2.0

"""Files and folders: upload, list, download, delete, and every guard."""

from __future__ import annotations

import hashlib

from fastapi.testclient import TestClient

from paper_boxing.common.schema import EntryType, ErrorBody, ErrorCode, FileListing, UploadResult
from tests.contract.conftest import Actors, bearer


def _code(resp) -> ErrorCode:
    return ErrorBody.model_validate(resp.json()).error.code


def _site(api: TestClient, headers: dict[str, str], name: str = "Files") -> str:
    resp = api.post("/api/v1/sites", json={"name": name}, headers=headers)
    assert resp.status_code == 201, resp.text
    return resp.json()["slug"]


def test_upload_list_download_round_trip(api: TestClient, actors: Actors) -> None:
    s = bearer(actors.session)
    slug = _site(api, s)
    png = b"\x89PNG\r\n\x1a\n" + bytes(range(256)) * 4
    resp = api.put(f"/api/v1/sites/{slug}/files/img/pic.png", content=png, headers=s)
    assert resp.status_code == 201, resp.text
    result = UploadResult.model_validate(resp.json())
    assert result.path == "img/pic.png"
    assert result.bytes == len(png)
    assert result.sha256 == hashlib.sha256(png).hexdigest()
    assert result.replaced is False

    root = FileListing.model_validate(api.get(f"/api/v1/sites/{slug}/files", headers=s).json())
    assert root.path == ""
    assert [(e.name, e.type) for e in root.entries] == [("img", EntryType.FOLDER)]
    assert root.entries[0].bytes == len(png)
    assert root.entries[0].sha256 is None

    folder = FileListing.model_validate(
        api.get(f"/api/v1/sites/{slug}/files", params={"path": "img/"}, headers=s).json()
    )
    assert folder.path == "img"
    assert [(e.name, e.type, e.bytes, e.sha256) for e in folder.entries] == [
        ("pic.png", EntryType.FILE, len(png), result.sha256)
    ]

    download = api.get(f"/api/v1/sites/{slug}/files/img/pic.png", headers=s)
    assert download.status_code == 200
    assert download.content == png
    assert download.headers["content-type"] == "image/png"
    assert download.headers["content-disposition"].startswith('attachment; filename="pic.png"')


def test_listing_order_is_folders_then_files_by_name(api: TestClient, actors: Actors) -> None:
    s = bearer(actors.session)
    slug = _site(api, s)
    for path in ("z.txt", "b/x.txt", "a.txt", "y/x.txt"):
        assert api.put(f"/api/v1/sites/{slug}/files/{path}", content=b"1", headers=s).status_code == 201
    listing = FileListing.model_validate(api.get(f"/api/v1/sites/{slug}/files", headers=s).json())
    assert [e.name for e in listing.entries] == ["b", "y", "a.txt", "z.txt"]


def test_overwrite_guard(api: TestClient, actors: Actors) -> None:
    s = bearer(actors.session)
    slug = _site(api, s)
    assert api.put(f"/api/v1/sites/{slug}/files/index.html", content=b"one", headers=s).status_code == 201
    resp = api.put(f"/api/v1/sites/{slug}/files/index.html", content=b"two", headers=s)
    assert resp.status_code == 409
    assert _code(resp) is ErrorCode.FILE_EXISTS
    assert api.get(f"/api/v1/sites/{slug}/files/index.html", headers=s).content == b"one"
    resp = api.put(
        f"/api/v1/sites/{slug}/files/index.html", content=b"two", params={"overwrite": "true"}, headers=s
    )
    assert resp.status_code == 200
    assert UploadResult.model_validate(resp.json()).replaced is True
    assert api.get(f"/api/v1/sites/{slug}/files/index.html", headers=s).content == b"two"


def test_empty_file_is_allowed(api: TestClient, actors: Actors) -> None:
    s = bearer(actors.session)
    slug = _site(api, s)
    resp = api.put(f"/api/v1/sites/{slug}/files/empty.txt", content=b"", headers=s)
    assert resp.status_code == 201
    assert UploadResult.model_validate(resp.json()).bytes == 0


def test_size_cap_is_413(api: TestClient, actors: Actors) -> None:
    s = bearer(actors.session)
    slug = _site(api, s)
    resp = api.put(f"/api/v1/sites/{slug}/files/big.bin", content=b"x" * (1024 * 1024 + 1), headers=s)
    assert resp.status_code == 413
    assert _code(resp) is ErrorCode.PAYLOAD_TOO_LARGE
    assert api.get(f"/api/v1/sites/{slug}/files", headers=s).json()["entries"] == []


def test_size_cap_precedes_conflicts_on_a_chunked_body(api: TestClient, actors: Actors) -> None:
    """A body sent without Content-Length onto an existing file, without overwrite: the cap answers
    before the conflict does, on both implementations, and the existing file is untouched."""
    s = bearer(actors.session)
    slug = _site(api, s)
    assert api.put(f"/api/v1/sites/{slug}/files/big.bin", content=b"small", headers=s).status_code == 201
    resp = api.put(
        f"/api/v1/sites/{slug}/files/big.bin",
        content=iter([b"x" * 600_000, b"y" * 600_000]),
        headers=s,
    )
    assert resp.status_code == 413
    assert _code(resp) is ErrorCode.PAYLOAD_TOO_LARGE
    assert api.get(f"/api/v1/sites/{slug}/files/big.bin", headers=s).content == b"small"


def test_invalid_paths_are_400(api: TestClient, actors: Actors) -> None:
    s = bearer(actors.session)
    slug = _site(api, s)
    # Dot segments are sent percent-encoded: an HTTP client normalises a literal `..` away before
    # the request leaves it, whereas the server decodes `%2e%2e` into the path it must refuse.
    for raw in ("%2e%2e/x", "a/%2e%2e/%2e%2e/x", "a/%2e/x", "a//x", "a%5cx", "a%00x"):
        resp = api.put(f"/api/v1/sites/{slug}/files/{raw}", content=b"x", headers=s)
        assert resp.status_code == 400, raw
        assert _code(resp) is ErrorCode.INVALID_PATH
        assert api.get(f"/api/v1/sites/{slug}/files/{raw}", headers=s).status_code == 400, raw
        assert api.delete(f"/api/v1/sites/{slug}/files/{raw}", headers=s).status_code == 400, raw
    resp = api.get(f"/api/v1/sites/{slug}/files", params={"path": "../"}, headers=s)
    assert resp.status_code == 400


def test_file_and_folder_are_not_interchangeable(api: TestClient, actors: Actors) -> None:
    s = bearer(actors.session)
    slug = _site(api, s)
    assert api.put(f"/api/v1/sites/{slug}/files/docs/a.txt", content=b"a", headers=s).status_code == 201
    # a folder where a file is expected
    for resp in (
        api.put(f"/api/v1/sites/{slug}/files/docs", content=b"x", headers=s),
        api.get(f"/api/v1/sites/{slug}/files/docs", headers=s),
        api.delete(f"/api/v1/sites/{slug}/files/docs", headers=s),
    ):
        assert resp.status_code == 409, resp.text
        assert _code(resp) is ErrorCode.NOT_A_FILE
    # a file where a folder is expected
    for resp in (
        api.get(f"/api/v1/sites/{slug}/files", params={"path": "docs/a.txt"}, headers=s),
        api.delete(f"/api/v1/sites/{slug}/folders/docs/a.txt", headers=s),
        api.put(f"/api/v1/sites/{slug}/files/docs/a.txt/below.txt", content=b"x", headers=s),
    ):
        assert resp.status_code == 409, resp.text
        assert _code(resp) is ErrorCode.NOT_A_FOLDER


def test_missing_things_are_404(api: TestClient, actors: Actors) -> None:
    s = bearer(actors.session)
    slug = _site(api, s)
    assert api.get(f"/api/v1/sites/{slug}/files/nope.txt", headers=s).status_code == 404
    assert api.delete(f"/api/v1/sites/{slug}/files/nope.txt", headers=s).status_code == 404
    assert api.delete(f"/api/v1/sites/{slug}/folders/nope", headers=s).status_code == 404
    assert api.get(f"/api/v1/sites/{slug}/files", params={"path": "nope"}, headers=s).status_code == 404
    assert api.get("/api/v1/sites/nosite/files", headers=s).status_code == 404
    assert api.put("/api/v1/sites/nosite/files/a.txt", content=b"x", headers=s).status_code == 404


def test_delete_file_and_folder(api: TestClient, actors: Actors) -> None:
    s = bearer(actors.session)
    slug = _site(api, s)
    for path in ("docs/a.txt", "docs/sub/b.txt", "top.txt"):
        assert api.put(f"/api/v1/sites/{slug}/files/{path}", content=b"1", headers=s).status_code == 201
    assert api.delete(f"/api/v1/sites/{slug}/files/top.txt", headers=s).status_code == 204
    assert api.get(f"/api/v1/sites/{slug}/files/top.txt", headers=s).status_code == 404

    resp = api.delete(f"/api/v1/sites/{slug}/folders/docs", headers=s)
    assert resp.status_code == 409
    assert _code(resp) is ErrorCode.FOLDER_NOT_EMPTY
    assert api.get(f"/api/v1/sites/{slug}/files/docs/a.txt", headers=s).status_code == 200

    assert api.delete(f"/api/v1/sites/{slug}/files/docs/sub/b.txt", headers=s).status_code == 204
    # the emptied folder still exists and is deletable without recursive
    sub = FileListing.model_validate(
        api.get(f"/api/v1/sites/{slug}/files", params={"path": "docs/sub"}, headers=s).json()
    )
    assert sub.entries == []
    assert api.delete(f"/api/v1/sites/{slug}/folders/docs/sub", headers=s).status_code == 204

    assert (
        api.delete(f"/api/v1/sites/{slug}/folders/docs", params={"recursive": "true"}, headers=s).status_code
        == 204
    )
    assert api.get(f"/api/v1/sites/{slug}/files", headers=s).json()["entries"] == []


def test_site_root_cannot_be_deleted_as_a_folder(api: TestClient, actors: Actors) -> None:
    s = bearer(actors.session)
    slug = _site(api, s)
    resp = api.delete(f"/api/v1/sites/{slug}/folders/", params={"recursive": "true"}, headers=s)
    assert resp.status_code in (400, 404, 405)


def test_content_types_on_download(api: TestClient, actors: Actors) -> None:
    s = bearer(actors.session)
    slug = _site(api, s)
    cases = {
        "index.html": "text/html",
        "style.css": "text/css",
        "app.js": "text/javascript",
        "font.woff2": "font/woff2",
        "logo.svg": "image/svg+xml",
        "blob.bin": "application/octet-stream",
    }
    for path in cases:
        assert api.put(f"/api/v1/sites/{slug}/files/{path}", content=b"x", headers=s).status_code == 201
    for path, expected in cases.items():
        content_type = api.get(f"/api/v1/sites/{slug}/files/{path}", headers=s).headers["content-type"]
        assert content_type.split(";")[0] == expected, path


def test_unicode_and_spaces_in_paths(api: TestClient, actors: Actors) -> None:
    s = bearer(actors.session)
    slug = _site(api, s)
    path = "docs/naïve page.html"
    resp = api.put(f"/api/v1/sites/{slug}/files/docs/na%C3%AFve%20page.html", content=b"<p>", headers=s)
    assert resp.status_code == 201, resp.text
    assert UploadResult.model_validate(resp.json()).path == path
    listing = FileListing.model_validate(
        api.get(f"/api/v1/sites/{slug}/files", params={"path": "docs"}, headers=s).json()
    )
    assert listing.entries[0].name == "naïve page.html"
    assert api.get(f"/api/v1/sites/{slug}/files/docs/na%C3%AFve%20page.html", headers=s).content == b"<p>"
