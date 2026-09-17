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
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from paper_boxing.backend import storage as storage_module
from paper_boxing.backend.storage import PathLocks
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
# The per-path lock


async def test_writes_to_one_path_are_serialised_and_other_paths_are_not() -> None:
    locks = PathLocks()
    order: list[str] = []

    async def hold(path: str, name: str, seconds: float) -> None:
        async with locks.hold("site", path, name):
            order.append(f"{name}:in")
            assert locks.holder("site", path) == name
            await asyncio.sleep(seconds)
            order.append(f"{name}:out")

    await asyncio.gather(hold("p", "first", 0.05), hold("p", "second", 0), hold("q", "other", 0))
    assert order == ["first:in", "other:in", "other:out", "first:out", "second:in", "second:out"]
    assert len(locks) == 0
    assert locks.holder("site", "p") is None
