# SPDX-FileCopyrightText: 2026 Gary Frattarola <garyf@parkviewlab.ai>
#
# SPDX-License-Identifier: MIT OR Apache-2.0

"""The backend's file storage: bytes on disk under sites/, path containment
(encoded traversal, symlinks, special files), the atomic replace and its
failure modes, the size cap, the intent guards, and the per-path lock."""

from __future__ import annotations

import asyncio
import errno
import hashlib
import os
import sqlite3
from collections.abc import AsyncIterator, Iterator
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from paper_boxing.backend import storage as storage_module
from paper_boxing.backend.db import Database
from paper_boxing.backend.storage import SiteLocks, Storage
from paper_boxing.common.errors import ApiError
from paper_boxing.common.schema import ErrorBody, ErrorCode
from tests._backend_helpers import backend_app, backend_config, bearer, create_site, login

MIB = 1024 * 1024


@pytest.fixture(scope="module")
def data_dir(tmp_path_factory: pytest.TempPathFactory) -> Path:
    return tmp_path_factory.mktemp("backend-storage")


@pytest.fixture(scope="module")
def client(data_dir: Path) -> Iterator[TestClient]:
    """One backend for the module, with a 1 MiB cap; unexpected exceptions become 500 responses."""
    app = backend_app(backend_config(data_dir / "data", max_upload_mb=1))
    with TestClient(app, base_url="http://localhost", raise_server_exceptions=False) as client:
        yield client


@pytest.fixture(scope="module")
def session(client: TestClient) -> str:
    return login(client)


@pytest.fixture(scope="module")
def s(session: str) -> dict[str, str]:
    return bearer(session)


def _sites(data_dir: Path) -> Path:
    return data_dir / "data" / "sites"


def _staging(data_dir: Path) -> Path:
    return data_dir / "data" / "staging"


def _code(resp: Any) -> ErrorCode:
    return ErrorBody.model_validate(resp.json()).error.code


# ---------------------------------------------------------------------------
# Bytes on disk


def test_upload_writes_the_bytes_under_sites(client: TestClient, s: dict[str, str], data_dir: Path) -> None:
    slug = create_site(client, login(client), "On Disk")
    content = bytes(range(256)) * 10
    resp = client.put(f"/api/v1/sites/{slug}/files/docs/data.bin", content=content, headers=s)
    assert resp.status_code == 201, resp.text
    on_disk = _sites(data_dir) / slug / "docs" / "data.bin"
    assert on_disk.read_bytes() == content
    assert resp.json()["sha256"] == hashlib.sha256(content).hexdigest()
    assert list(_staging(data_dir).iterdir()) == []


def test_chunked_upload_without_content_length_streams_to_disk(
    client: TestClient, s: dict[str, str], data_dir: Path
) -> None:
    slug = create_site(client, s["Authorization"].split()[1], "Chunked")
    parts = [b"a" * 300_000, b"b" * 300_000]
    resp = client.put(f"/api/v1/sites/{slug}/files/big.bin", content=iter(parts), headers=s)
    assert resp.status_code == 201, resp.text
    assert resp.json()["bytes"] == 600_000
    assert (_sites(data_dir) / slug / "big.bin").read_bytes() == b"".join(parts)


def test_create_site_never_adopts_an_existing_directory(
    client: TestClient, s: dict[str, str], data_dir: Path
) -> None:
    """A leftover sites/<slug>/ on the volume is not a site: creating one there is 409, and it stays as it was."""
    leftover = _sites(data_dir) / "leftover"
    leftover.mkdir()
    (leftover / "index.html").write_text("old")
    resp = client.post("/api/v1/sites", json={"name": "Leftover"}, headers=s)
    assert resp.status_code == 409, resp.text
    assert _code(resp) is ErrorCode.SITE_EXISTS
    assert "volume" in resp.json()["error"]["message"]
    assert client.get("/api/v1/sites/leftover", headers=s).status_code == 404
    assert (leftover / "index.html").read_text() == "old"


def test_create_site_removes_its_folder_when_the_row_cannot_be_inserted(
    client: TestClient, s: dict[str, str], data_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def refuse(self: Database, site: Any) -> None:
        raise sqlite3.OperationalError("database or disk is full")

    monkeypatch.setattr(Database, "insert_site", refuse)
    resp = client.post("/api/v1/sites", json={"name": "Unrecorded"}, headers=s)
    assert resp.status_code == 500
    assert not (_sites(data_dir) / "unrecorded").exists()
    monkeypatch.undo()
    assert client.get("/api/v1/sites/unrecorded", headers=s).status_code == 404
    assert client.post("/api/v1/sites", json={"name": "Unrecorded"}, headers=s).status_code == 201


def test_delete_site_removes_the_tree(client: TestClient, s: dict[str, str], data_dir: Path) -> None:
    slug = create_site(client, s["Authorization"].split()[1], "Doomed Tree")
    for path in ("index.html", "css/a.css", "img/x/y.png"):
        assert client.put(f"/api/v1/sites/{slug}/files/{path}", content=b"x", headers=s).status_code == 201
    assert (_sites(data_dir) / slug / "img" / "x" / "y.png").is_file()
    resp = client.delete(f"/api/v1/sites/{slug}", params={"confirm": slug}, headers=s)
    assert resp.status_code == 204
    assert not (_sites(data_dir) / slug).exists()
    assert list(_staging(data_dir).iterdir()) == []
    assert client.get(f"/api/v1/sites/{slug}", headers=s).status_code == 404


# ---------------------------------------------------------------------------
# Containment


def test_encoded_traversal_is_refused_and_nothing_is_written(
    client: TestClient, s: dict[str, str], data_dir: Path
) -> None:
    slug = create_site(client, s["Authorization"].split()[1], "Traversal")
    for raw in (
        "%2e%2e/planted.txt",
        "%2e%2e/%2e%2e/planted.txt",
        "docs/%2e%2e/%2e%2e/%2e%2e/planted.txt",
        "%2e%2e%2f%2e%2e%2fplanted.txt",
        "docs/%2e/planted.txt",
    ):
        resp = client.put(f"/api/v1/sites/{slug}/files/{raw}", content=b"x", headers=s)
        assert resp.status_code == 400, raw
        assert _code(resp) is ErrorCode.INVALID_PATH
        assert client.get(f"/api/v1/sites/{slug}/files/{raw}", headers=s).status_code == 400, raw
        assert client.delete(f"/api/v1/sites/{slug}/files/{raw}", headers=s).status_code == 400, raw
        assert client.delete(f"/api/v1/sites/{slug}/folders/{raw}", headers=s).status_code == 400, raw
    assert client.get(f"/api/v1/sites/{slug}/files", params={"path": "../.."}, headers=s).status_code == 400
    for planted in (
        data_dir / "planted.txt",
        data_dir / "data" / "planted.txt",
        _sites(data_dir) / "planted.txt",
    ):
        assert not planted.exists(), planted


def test_symlink_planted_inside_a_site_pointing_outside_is_refused(
    client: TestClient, s: dict[str, str], data_dir: Path
) -> None:
    slug = create_site(client, s["Authorization"].split()[1], "Escape")
    assert client.put(f"/api/v1/sites/{slug}/files/real.txt", content=b"real", headers=s).status_code == 201
    outside = data_dir / "outside.txt"
    outside.write_bytes(b"secret")
    (_sites(data_dir) / slug / "escape.txt").symlink_to(outside)

    resp = client.get(f"/api/v1/sites/{slug}/files/escape.txt", headers=s)
    assert resp.status_code == 400
    assert _code(resp) is ErrorCode.INVALID_PATH
    resp = client.put(
        f"/api/v1/sites/{slug}/files/escape.txt", content=b"x", params={"overwrite": "true"}, headers=s
    )
    assert resp.status_code == 400
    assert outside.read_bytes() == b"secret"
    assert client.delete(f"/api/v1/sites/{slug}/files/escape.txt", headers=s).status_code == 400
    assert (_sites(data_dir) / slug / "escape.txt").is_symlink()
    listing = client.get(f"/api/v1/sites/{slug}/files", headers=s).json()
    assert [e["name"] for e in listing["entries"]] == ["real.txt"]  # the symlink is not listed
    site = client.get(f"/api/v1/sites/{slug}", headers=s).json()
    assert site["file_count"] == 1 and site["bytes"] == 4


def test_symlinked_folder_as_a_path_segment_is_refused(
    client: TestClient, s: dict[str, str], data_dir: Path
) -> None:
    slug = create_site(client, s["Authorization"].split()[1], "Escape Dir")
    outside = data_dir / "outside-dir"
    outside.mkdir()
    (_sites(data_dir) / slug / "link").symlink_to(outside, target_is_directory=True)
    resp = client.put(f"/api/v1/sites/{slug}/files/link/x.txt", content=b"x", headers=s)
    assert resp.status_code == 400
    assert _code(resp) is ErrorCode.INVALID_PATH
    assert not (outside / "x.txt").exists()
    assert client.get(f"/api/v1/sites/{slug}/files", params={"path": "link"}, headers=s).status_code == 400
    assert (
        client.delete(
            f"/api/v1/sites/{slug}/folders/link", params={"recursive": "true"}, headers=s
        ).status_code
        == 400
    )
    assert outside.is_dir()


def test_symlink_pointing_inside_the_site_is_refused_as_well(
    client: TestClient, s: dict[str, str], data_dir: Path
) -> None:
    """Nothing the API writes is a symlink, so any symlink means the volume was changed by hand."""
    slug = create_site(client, s["Authorization"].split()[1], "Inner Link")
    assert client.put(f"/api/v1/sites/{slug}/files/a.txt", content=b"a", headers=s).status_code == 201
    (_sites(data_dir) / slug / "b.txt").symlink_to(_sites(data_dir) / slug / "a.txt")
    assert client.get(f"/api/v1/sites/{slug}/files/b.txt", headers=s).status_code == 400
    assert client.get(f"/api/v1/sites/{slug}/files/a.txt", headers=s).content == b"a"


def test_special_files_are_refused_and_not_listed(
    client: TestClient, s: dict[str, str], data_dir: Path
) -> None:
    slug = create_site(client, s["Authorization"].split()[1], "Fifo")
    os.mkfifo(_sites(data_dir) / slug / "pipe")
    assert client.get(f"/api/v1/sites/{slug}/files/pipe", headers=s).status_code == 400
    resp = client.put(
        f"/api/v1/sites/{slug}/files/pipe", content=b"x", params={"overwrite": "true"}, headers=s
    )
    assert resp.status_code == 400
    assert (
        client.put(f"/api/v1/sites/{slug}/files/pipe/below.txt", content=b"x", headers=s).status_code == 400
    )
    assert client.delete(f"/api/v1/sites/{slug}/files/pipe", headers=s).status_code == 400
    assert client.get(f"/api/v1/sites/{slug}/files", headers=s).json()["entries"] == []


# ---------------------------------------------------------------------------
# The atomic replace and its failures


class _FailingFile:
    """Wraps the real staging file; the first write raises `exc`."""

    def __init__(self, real: Any, exc: BaseException) -> None:
        self._real = real
        self._exc = exc

    def __enter__(self) -> _FailingFile:
        self._real.__enter__()
        return self

    def __exit__(self, *args: object) -> None:
        self._real.__exit__(*args)

    def write(self, chunk: bytes) -> int:
        raise self._exc

    def flush(self) -> None:
        self._real.flush()

    def fileno(self) -> int:
        return self._real.fileno()


def _upload_v1(client: TestClient, s: dict[str, str], name: str) -> tuple[str, str]:
    slug = create_site(client, s["Authorization"].split()[1], name)
    path = f"/api/v1/sites/{slug}/files/index.html"
    assert client.put(path, content=b"version one", headers=s).status_code == 201
    return slug, path


def test_a_failure_during_the_write_leaves_the_old_file_whole(
    client: TestClient, s: dict[str, str], data_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    slug, path = _upload_v1(client, s, "Atomic Write")
    on_disk = _sites(data_dir) / slug / "index.html"

    real_open = storage_module.open_for_writing
    monkeypatch.setattr(
        storage_module,
        "open_for_writing",
        lambda p: _FailingFile(real_open(p), RuntimeError("simulated failure during the write")),
    )
    resp = client.put(path, content=b"version two", params={"overwrite": "true"}, headers=s)
    assert resp.status_code == 500
    assert _code(resp) is ErrorCode.INTERNAL_ERROR
    assert on_disk.read_bytes() == b"version one"
    assert list(_staging(data_dir).iterdir()) == []

    monkeypatch.undo()
    resp = client.put(path, content=b"version two", params={"overwrite": "true"}, headers=s)
    assert resp.status_code == 200
    assert on_disk.read_bytes() == b"version two"


def test_disk_full_during_the_write_is_507_with_nothing_half_written(
    client: TestClient, s: dict[str, str], data_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    slug, path = _upload_v1(client, s, "Disk Full")
    on_disk = _sites(data_dir) / slug / "index.html"
    full = OSError(errno.ENOSPC, "No space left on device")
    real_open = storage_module.open_for_writing
    monkeypatch.setattr(storage_module, "open_for_writing", lambda p: _FailingFile(real_open(p), full))
    resp = client.put(path, content=b"version two", params={"overwrite": "true"}, headers=s)
    assert resp.status_code == 507
    assert _code(resp) is ErrorCode.INSUFFICIENT_STORAGE
    assert on_disk.read_bytes() == b"version one"
    assert list(_staging(data_dir).iterdir()) == []
    # a new file that never made it does not appear either
    resp = client.put(f"/api/v1/sites/{slug}/files/new.txt", content=b"n", headers=s)
    assert resp.status_code == 507
    assert not (_sites(data_dir) / slug / "new.txt").exists()
    assert client.get(f"/api/v1/sites/{slug}/files", headers=s).json()["entries"][0]["name"] == "index.html"


def test_a_failure_at_the_replace_leaves_the_old_file_whole(
    client: TestClient, s: dict[str, str], data_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    slug, path = _upload_v1(client, s, "Atomic Replace")
    on_disk = _sites(data_dir) / slug / "index.html"

    def refuse(src: Any, dst: Any) -> None:
        raise OSError(errno.EDQUOT, "Disc quota exceeded")

    monkeypatch.setattr(os, "replace", refuse)
    resp = client.put(path, content=b"version two", params={"overwrite": "true"}, headers=s)
    assert resp.status_code == 507
    assert on_disk.read_bytes() == b"version one"
    assert list(_staging(data_dir).iterdir()) == []


def test_a_full_volume_during_create_site_is_507_and_no_site_exists(
    client: TestClient, s: dict[str, str], data_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    real_mkdir = Path.mkdir

    def full(self: Path, *args: Any, **kwargs: Any) -> None:
        if self.name == "no-room":
            raise OSError(errno.ENOSPC, "No space left on device")
        real_mkdir(self, *args, **kwargs)

    monkeypatch.setattr(Path, "mkdir", full)
    resp = client.post("/api/v1/sites", json={"name": "No Room"}, headers=s)
    assert resp.status_code == 507, resp.text
    assert _code(resp) is ErrorCode.INSUFFICIENT_STORAGE
    monkeypatch.undo()
    assert client.get("/api/v1/sites/no-room", headers=s).status_code == 404
    assert not (_sites(data_dir) / "no-room").exists()


def test_size_cap_on_a_chunked_body_is_413_and_staging_is_clean(
    client: TestClient, s: dict[str, str], data_dir: Path
) -> None:
    slug = create_site(client, s["Authorization"].split()[1], "Too Big")
    resp = client.put(
        f"/api/v1/sites/{slug}/files/big.bin", content=iter([b"x" * 600_000, b"y" * 600_000]), headers=s
    )
    assert resp.status_code == 413
    assert _code(resp) is ErrorCode.PAYLOAD_TOO_LARGE
    assert not (_sites(data_dir) / slug / "big.bin").exists()
    assert list(_staging(data_dir).iterdir()) == []
    # exactly the cap is accepted; one byte over, declared up front, is refused before anything is read
    assert client.put(f"/api/v1/sites/{slug}/files/cap.bin", content=b"x" * MIB, headers=s).status_code == 201
    resp = client.put(f"/api/v1/sites/{slug}/files/over.bin", content=b"x" * (MIB + 1), headers=s)
    assert resp.status_code == 413
    assert list(_staging(data_dir).iterdir()) == []


def test_startup_removes_leftovers_in_staging(tmp_path: Path) -> None:
    config = backend_config(tmp_path / "data")
    config.staging_dir.mkdir(parents=True)
    (config.staging_dir / "0123abcd.part").write_bytes(b"half an upload")
    parked = config.staging_dir / "old-site.deleting-ab12"
    parked.mkdir()
    (parked / "index.html").write_text("x")
    with TestClient(backend_app(config)):
        assert list(config.staging_dir.iterdir()) == []


# ---------------------------------------------------------------------------
# The file index behind listings and totals


def _rows(data_dir: Path, slug: str) -> list[tuple[str, int, str]]:
    conn = sqlite3.connect(data_dir / "data" / "paper-boxing.sqlite3")
    try:
        return conn.execute(
            "SELECT path, bytes, sha256 FROM files WHERE site = ? ORDER BY path", (slug,)
        ).fetchall()
    finally:
        conn.close()


def test_listing_reads_the_index_and_hashes_only_what_changed(
    client: TestClient, s: dict[str, str], data_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    slug = create_site(client, s["Authorization"].split()[1], "Indexed")
    assert (
        client.put(f"/api/v1/sites/{slug}/files/docs/a.txt", content=b"alpha", headers=s).status_code == 201
    )
    assert client.put(f"/api/v1/sites/{slug}/files/docs/b.txt", content=b"beta", headers=s).status_code == 201
    docs = _sites(data_dir) / slug / "docs"
    hashed: list[str] = []
    real_sha256_file = storage_module.sha256_file
    monkeypatch.setattr(
        storage_module, "sha256_file", lambda path: (hashed.append(path.name), real_sha256_file(path))[1]
    )

    def listing() -> dict[str, dict[str, Any]]:
        resp = client.get(f"/api/v1/sites/{slug}/files", params={"path": "docs"}, headers=s)
        assert resp.status_code == 200, resp.text
        return {e["name"]: e for e in resp.json()["entries"]}

    # unchanged files: the digests come from the index, nothing is read
    entries = listing()
    assert entries["a.txt"]["sha256"] == hashlib.sha256(b"alpha").hexdigest()
    assert hashed == []

    # a file changed by other means: its size and mtime no longer match its row, so it is re-hashed once
    (docs / "a.txt").write_bytes(b"ALPHA!")
    st = os.stat(docs / "a.txt")
    os.utime(docs / "a.txt", ns=(st.st_atime_ns, st.st_mtime_ns + 2_000_000_000))
    entries = listing()
    assert entries["a.txt"]["sha256"] == hashlib.sha256(b"ALPHA!").hexdigest()
    assert entries["a.txt"]["bytes"] == 6
    assert hashed == ["a.txt"]
    listing()
    assert hashed == ["a.txt"]

    # a file placed by other means: listed, hashed once, indexed from then on
    (docs / "c.txt").write_bytes(b"gamma")
    entries = listing()
    assert sorted(entries) == ["a.txt", "b.txt", "c.txt"]
    assert entries["c.txt"]["sha256"] == hashlib.sha256(b"gamma").hexdigest()
    assert hashed == ["a.txt", "c.txt"]
    listing()
    assert hashed == ["a.txt", "c.txt"]

    # a file removed by other means: gone from the listing, its row dropped, the totals follow
    (docs / "b.txt").unlink()
    assert sorted(listing()) == ["a.txt", "c.txt"]
    assert [(path, size) for path, size, _ in _rows(data_dir, slug)] == [("docs/a.txt", 6), ("docs/c.txt", 5)]
    site = client.get(f"/api/v1/sites/{slug}", headers=s).json()
    assert (site["file_count"], site["bytes"]) == (2, 11)


def test_totals_and_folder_entries_come_from_the_index(
    client: TestClient, s: dict[str, str], data_dir: Path
) -> None:
    slug = create_site(client, s["Authorization"].split()[1], "Totals")
    for path, content in (("a.txt", b"abc"), ("d/b.txt", b"de"), ("d/e/c.txt", b"fghi")):
        assert client.put(f"/api/v1/sites/{slug}/files/{path}", content=content, headers=s).status_code == 201
    site = client.get(f"/api/v1/sites/{slug}", headers=s).json()
    assert (site["file_count"], site["bytes"]) == (3, 9)
    root = {e["name"]: e for e in client.get(f"/api/v1/sites/{slug}/files", headers=s).json()["entries"]}
    assert root["d"]["type"] == "folder" and root["d"]["bytes"] == 6
    assert [path for path, _, _ in _rows(data_dir, slug)] == ["a.txt", "d/b.txt", "d/e/c.txt"]

    assert client.delete(f"/api/v1/sites/{slug}/files/a.txt", headers=s).status_code == 204
    site = client.get(f"/api/v1/sites/{slug}", headers=s).json()
    assert (site["file_count"], site["bytes"]) == (2, 6)
    assert (
        client.delete(f"/api/v1/sites/{slug}/folders/d", params={"recursive": "true"}, headers=s).status_code
        == 204
    )
    site = client.get(f"/api/v1/sites/{slug}", headers=s).json()
    assert (site["file_count"], site["bytes"]) == (0, 0)
    assert _rows(data_dir, slug) == []

    assert client.put(f"/api/v1/sites/{slug}/files/x.txt", content=b"x", headers=s).status_code == 201
    assert client.delete(f"/api/v1/sites/{slug}", params={"confirm": slug}, headers=s).status_code == 204
    assert _rows(data_dir, slug) == []  # the site's rows went with its row


# ---------------------------------------------------------------------------
# The intent guards: 409 and 400


def test_409_guards(client: TestClient, s: dict[str, str]) -> None:
    slug = create_site(client, s["Authorization"].split()[1], "Guards")
    assert client.put(f"/api/v1/sites/{slug}/files/docs/a.txt", content=b"a", headers=s).status_code == 201
    cases = {
        ErrorCode.FILE_EXISTS: client.put(f"/api/v1/sites/{slug}/files/docs/a.txt", content=b"b", headers=s),
        ErrorCode.NOT_A_FILE: client.put(f"/api/v1/sites/{slug}/files/docs", content=b"b", headers=s),
        ErrorCode.NOT_A_FOLDER: client.put(
            f"/api/v1/sites/{slug}/files/docs/a.txt/b.txt", content=b"b", headers=s
        ),
        ErrorCode.FOLDER_NOT_EMPTY: client.delete(f"/api/v1/sites/{slug}/folders/docs", headers=s),
        ErrorCode.SITE_EXISTS: client.post("/api/v1/sites", json={"name": "GUARDS"}, headers=s),
    }
    for code, resp in cases.items():
        assert resp.status_code == 409, code
        assert _code(resp) is code
    assert client.get(f"/api/v1/sites/{slug}/files/docs/a.txt", headers=s).content == b"a"
    assert (
        client.get(f"/api/v1/sites/{slug}/files", params={"path": "docs/a.txt"}, headers=s).status_code == 409
    )
    assert client.delete(f"/api/v1/sites/{slug}/folders/docs/a.txt", headers=s).status_code == 409
    assert client.delete(f"/api/v1/sites/{slug}/files/docs", headers=s).status_code == 409


def test_400_guards(client: TestClient, s: dict[str, str]) -> None:
    slug = create_site(client, s["Authorization"].split()[1], "Four Hundred")
    resp = client.post("/api/v1/sites", json={"name": "¡¡¡"}, headers=s)
    assert resp.status_code == 400 and _code(resp) is ErrorCode.INVALID_NAME
    for raw in ("a//b.txt", "a%5cb.txt", "a%00b.txt", "a%01b.txt", "a%7fb.txt", "x" * 256 + ".txt"):
        resp = client.put(f"/api/v1/sites/{slug}/files/{raw}", content=b"x", headers=s)
        assert resp.status_code == 400, raw
        assert _code(resp) is ErrorCode.INVALID_PATH
    # A newline never reaches the validator: Starlette's path parameter does not match one, so the
    # framework answers 404 (the fake backend does the same); the error body still has the shape.
    resp = client.put(f"/api/v1/sites/{slug}/files/a%0ab.txt", content=b"x", headers=s)
    assert resp.status_code == 404 and _code(resp) is ErrorCode.NOT_FOUND
    resp = client.put(f"/api/v1/sites/{slug}/files/", content=b"x", headers=s)
    assert resp.status_code == 400 and _code(resp) is ErrorCode.INVALID_PATH
    resp = client.delete(f"/api/v1/sites/{slug}/folders/", params={"recursive": "true"}, headers=s)
    assert resp.status_code == 400 and _code(resp) is ErrorCode.INVALID_PATH
    resp = client.delete(f"/api/v1/sites/{slug}", params={"confirm": "wrong"}, headers=s)
    assert resp.status_code == 400 and _code(resp) is ErrorCode.CONFIRM_MISMATCH
    resp = client.delete(f"/api/v1/sites/{slug}", headers=s)
    assert resp.status_code == 400 and _code(resp) is ErrorCode.CONFIRM_MISMATCH
    assert client.get(f"/api/v1/sites/{slug}", headers=s).status_code == 200


# ---------------------------------------------------------------------------
# The per-site lock, and the deletions it serialises with uploads


async def test_changes_to_one_site_are_serialised_and_other_sites_are_not() -> None:
    locks = SiteLocks()
    order: list[str] = []

    async def hold(slug: str, name: str, seconds: float) -> None:
        async with locks.hold(slug):
            order.append(f"{name}:in")
            assert locks.is_locked(slug)
            await asyncio.sleep(seconds)
            order.append(f"{name}:out")

    await asyncio.gather(hold("a", "first", 0.05), hold("a", "second", 0), hold("b", "other", 0))
    assert order == ["first:in", "other:in", "other:out", "first:out", "second:in", "second:out"]
    assert len(locks) == 0
    assert not locks.is_locked("a")


@pytest.fixture
def raw_storage(tmp_path: Path) -> Iterator[Storage]:
    """A storage over its own directories and database, driven directly from a coroutine."""
    db = Database.open(tmp_path / "paper-boxing.sqlite3")
    sites = tmp_path / "sites"
    staging = tmp_path / "staging"
    sites.mkdir()
    staging.mkdir()
    try:
        yield Storage(sites, staging, max_upload_bytes=MIB, db=db)
    finally:
        db.close()


async def _chunks(*parts: bytes) -> AsyncIterator[bytes]:
    for part in parts:
        yield part


async def _parked_upload(storage: Storage, slug: str, path: str) -> tuple[asyncio.Task[Any], asyncio.Event]:
    """Start an upload whose body stops after its first chunk until `release` is set, and wait until
    the first chunk is in staging."""
    release = asyncio.Event()

    async def body() -> AsyncIterator[bytes]:
        yield b"first half,"
        await release.wait()
        yield b" second half"

    task = asyncio.create_task(storage.write_file(slug, path, body(), overwrite=False, declared_length=None))
    for _ in range(200):
        if any(storage.staging_dir.glob("*.part")):
            break
        await asyncio.sleep(0.005)
    assert any(storage.staging_dir.glob("*.part")), "the upload never reached staging"
    return task, release


async def test_an_upload_in_flight_does_not_survive_a_site_deletion(raw_storage: Storage) -> None:
    """Reproduces the review's finding: a body parked mid-stream while the site is deleted must not
    recreate sites/<slug>/ after the row is gone (an orphan nginx would serve and no route could reach)."""
    storage = raw_storage
    await storage.create_site("race", "Race", datetime.now(UTC))
    task, release = await _parked_upload(storage, "race", "index.html")
    await storage.delete_site("race")
    assert not (storage.sites_dir / "race").exists()
    release.set()
    with pytest.raises(ApiError) as exc:
        await task
    assert exc.value.status == 404
    assert not (storage.sites_dir / "race").exists()
    assert list(storage.staging_dir.iterdir()) == []


async def test_an_upload_in_flight_and_a_folder_deletion_run_one_after_the_other(
    raw_storage: Storage,
) -> None:
    """The deletion completes whole (no ENOTEMPTY from a file landing mid-rmtree); the upload then
    lands as if it had started after it."""
    storage = raw_storage
    await storage.create_site("race", "Race", datetime.now(UTC))
    await storage.write_file("race", "docs/old.txt", _chunks(b"old"), overwrite=False, declared_length=None)
    task, release = await _parked_upload(storage, "race", "docs/new.txt")
    await storage.delete_folder("race", "docs", recursive=True)
    assert not (storage.sites_dir / "race" / "docs").exists()
    release.set()
    result = await task
    assert result.replaced is False and result.bytes == len(b"first half, second half")
    assert [p.name for p in (storage.sites_dir / "race" / "docs").iterdir()] == ["new.txt"]
    assert list(storage.staging_dir.iterdir()) == []


async def test_the_lock_is_not_held_while_the_body_streams(raw_storage: Storage) -> None:
    storage = raw_storage
    await storage.create_site("race", "Race", datetime.now(UTC))
    task, release = await _parked_upload(storage, "race", "a.txt")
    assert not storage.locks.is_locked("race")
    release.set()
    await task
    assert (storage.sites_dir / "race" / "a.txt").read_bytes() == b"first half, second half"
