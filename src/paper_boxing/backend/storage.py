# SPDX-FileCopyrightText: 2026 Gary Frattarola <garyf@parkviewlab.ai>
#
# SPDX-License-Identifier: MIT OR Apache-2.0

"""The served tree, `sites/<slug>/...`, and the staging area beside it
(docs/design.md section 4).

Path safety. A path arrives already validated by the contract's rules
(`paper_boxing.common.naming`); here every existing component below the
site is inspected with `lstat` and the whole path is resolved on disk and
checked to still lie inside the site, as `safe_subpath` does in
deco-assaying. Anything that passes through a symbolic link, resolves
outside the site, or is neither a regular file nor a directory is refused as
`400 invalid_path`. Nothing the API writes is ever a symlink, so a symlink
inside a site means the volume was changed by hand, and the API leaves it
alone; listings skip such entries.

Writes. An upload streams into `staging/`, on the same filesystem, under the
size cap; the file is fsynced and moved into place with `os.replace`, so a
reader (nginx included) sees the old file or the new one and never a partial
one, and a failure of any kind leaves the old file whole and the staging file
removed. Writes to one path are serialised by a named lock, after
ebony-enriching's mutex; it is an `asyncio.Lock` rather than a threading one
because the critical section awaits the request body. A disk-full or quota
error is `507 insufficient_storage` with nothing half-written.
"""

from __future__ import annotations

import asyncio
import errno
import hashlib
import logging
import os
import secrets
import shutil
import stat
from collections.abc import AsyncIterator, Iterator
from contextlib import asynccontextmanager, suppress
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import Enum
from pathlib import Path
from typing import BinaryIO

from paper_boxing.common.errors import ApiError
from paper_boxing.common.schema import EntryType, ErrorCode, FileEntry, FileListing, UploadResult

logger = logging.getLogger(__name__)

READ_CHUNK = 1024 * 1024
_STORAGE_FULL = frozenset({errno.ENOSPC, errno.EDQUOT})


class Kind(Enum):
    """What `lstat` finds at a path. SPECIAL covers symlinks, devices, FIFOs and sockets."""

    MISSING = "missing"
    FILE = "file"
    FOLDER = "folder"
    SPECIAL = "special"


def kind_of(path: Path) -> Kind:
    try:
        mode = os.lstat(path).st_mode
    except (FileNotFoundError, NotADirectoryError):
        return Kind.MISSING
    if stat.S_ISREG(mode):
        return Kind.FILE
    if stat.S_ISDIR(mode):
        return Kind.FOLDER
    return Kind.SPECIAL


def open_for_writing(path: Path) -> BinaryIO:
    """Open a staging file for exclusive creation. A module-level function so a test can make it fail."""
    return open(path, "xb")


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for chunk in iter(lambda: handle.read(READ_CHUNK), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _unlink(path: Path) -> None:
    with suppress(OSError):
        path.unlink(missing_ok=True)


def _invalid_path(path: str, why: str) -> ApiError:
    return ApiError(400, ErrorCode.INVALID_PATH, f"invalid path {path!r}: {why}")


def _mtime(timestamp: float) -> datetime:
    return datetime.fromtimestamp(timestamp, UTC)


@dataclass
class _LockEntry:
    lock: asyncio.Lock
    holder: str | None = None
    waiting: int = 0


class PathLocks:
    """One named `asyncio.Lock` per (site, path), created on first use and dropped when nobody holds or
    awaits it, so writes to one path run one at a time while writes to different paths proceed together."""

    def __init__(self) -> None:
        self._entries: dict[tuple[str, str], _LockEntry] = {}

    @asynccontextmanager
    async def hold(self, slug: str, path: str, holder: str) -> AsyncIterator[None]:
        key = (slug, path)
        entry = self._entries.get(key)
        if entry is None:
            entry = self._entries[key] = _LockEntry(asyncio.Lock())
        entry.waiting += 1
        try:
            async with entry.lock:
                entry.holder = holder
                try:
                    yield
                finally:
                    entry.holder = None
        finally:
            entry.waiting -= 1
            if entry.waiting == 0 and self._entries.get(key) is entry:
                del self._entries[key]

    def holder(self, slug: str, path: str) -> str | None:
        entry = self._entries.get((slug, path))
        return None if entry is None else entry.holder

    def __len__(self) -> int:
        return len(self._entries)


@dataclass(frozen=True)
class TreeStats:
    file_count: int
    bytes: int
    newest_mtime: float


def tree_stats(directory: Path) -> TreeStats:
    """Counts over the regular files below `directory`, folders included in the newest time;
    symlinks and special files are skipped, and nothing is followed."""
    newest = os.lstat(directory).st_mtime
    count = total = 0
    stack = [directory]
    while stack:
        current = stack.pop()
        with os.scandir(current) as entries:
            for entry in entries:
                try:
                    st = entry.stat(follow_symlinks=False)
                except OSError:
                    continue
                if stat.S_ISDIR(st.st_mode):
                    newest = max(newest, st.st_mtime)
                    stack.append(Path(entry.path))
                elif stat.S_ISREG(st.st_mode):
                    count += 1
                    total += st.st_size
                    newest = max(newest, st.st_mtime)
    return TreeStats(file_count=count, bytes=total, newest_mtime=newest)


class Storage:
    """The site trees under `sites_dir`, with `staging_dir` beside them on the same filesystem."""

    def __init__(self, sites_dir: Path, staging_dir: Path, *, max_upload_bytes: int) -> None:
        self._sites_dir = sites_dir
        self._staging_dir = staging_dir
        self._max_upload_bytes = max_upload_bytes
        self.locks = PathLocks()

    @property
    def sites_dir(self) -> Path:
        return self._sites_dir

    @property
    def staging_dir(self) -> Path:
        return self._staging_dir

    @property
    def max_upload_bytes(self) -> int:
        return self._max_upload_bytes

    def ensure_layout(self) -> None:
        self._sites_dir.mkdir(parents=True, exist_ok=True)
        self._staging_dir.mkdir(parents=True, exist_ok=True)

    def clean_staging(self) -> int:
        """Remove whatever a previous run left in `staging/`: nothing there is ever in progress after a start."""
        removed = 0
        for entry in list(self._staging_dir.iterdir()):
            if entry.is_dir() and not entry.is_symlink():
                shutil.rmtree(entry, ignore_errors=True)
            else:
                _unlink(entry)
            removed += 1
        if removed:
            logger.info("removed %d leftover entries from staging", removed)
        return removed

    # ---- paths ----

    def site_dir(self, slug: str) -> Path:
        return self._sites_dir / slug

    def resolve(self, slug: str, path: str) -> Path:
        """The on-disk location of a canonical `path` ("" for the root) inside the site.

        Refuses, as `400 invalid_path`, a site folder or a path component that is a symbolic
        link, and a path whose resolved form lies outside the site.
        """
        root = self.site_dir(slug)
        with suppress(FileNotFoundError, NotADirectoryError):
            if stat.S_ISLNK(os.lstat(root).st_mode):
                raise _invalid_path(path, "the site folder is a symbolic link")
        current = root
        for segment in path.split("/") if path else ():
            current = current / segment
            try:
                mode = os.lstat(current).st_mode
            except (FileNotFoundError, NotADirectoryError):
                break  # the rest does not exist yet; there is nothing to follow
            if stat.S_ISLNK(mode):
                raise _invalid_path(path, "the path passes through a symbolic link")
        target = root / path if path else root
        try:
            target.resolve(strict=False).relative_to(root.resolve(strict=False))
        except ValueError:
            raise _invalid_path(path, "the path escapes the site") from None
        return target

    def _relative(self, slug: str, target: Path) -> str:
        return target.relative_to(self.site_dir(slug)).as_posix()

    # ---- sites ----

    def create_site(self, slug: str) -> None:
        self.site_dir(slug).mkdir(parents=True, exist_ok=True)

    def site_totals(self, slug: str) -> tuple[int, int]:
        """(file count, bytes) of a site; (0, 0) when its folder is missing on disk."""
        root = self.site_dir(slug)
        if kind_of(root) is not Kind.FOLDER:
            return 0, 0
        stats = tree_stats(root)
        return stats.file_count, stats.bytes

    async def delete_site(self, slug: str) -> None:
        """Move the site's folder into staging in one rename, so nginx stops serving it at once, then
        remove it there."""
        root = self.site_dir(slug)
        if kind_of(root) is Kind.MISSING:
            return
        parked = self._staging_dir / f"{slug}.deleting-{secrets.token_hex(4)}"
        os.replace(root, parked)
        if kind_of(parked) is Kind.FOLDER:
            await asyncio.to_thread(shutil.rmtree, parked, ignore_errors=True)
        else:
            _unlink(parked)

    # ---- listing ----

    def list_folder(self, slug: str, folder: str) -> FileListing:
        target = self.resolve(slug, folder)
        kind = kind_of(target)
        if kind is Kind.FILE:
            raise ApiError(409, ErrorCode.NOT_A_FOLDER, f"{folder!r} is a file")
        if kind is Kind.SPECIAL:
            raise _invalid_path(folder, "not a regular file or a folder")
        if kind is Kind.MISSING:
            if folder == "":
                return FileListing(site=slug, path="", entries=[])  # a site whose folder is not on disk
            raise ApiError(404, ErrorCode.NOT_FOUND, f"no folder {folder!r} in site {slug!r}")
        folders: list[FileEntry] = []
        files: list[FileEntry] = []
        for name, path, st in self._scan(target):
            if stat.S_ISDIR(st.st_mode):
                stats = tree_stats(path)
                folders.append(
                    FileEntry(
                        name=name,
                        type=EntryType.FOLDER,
                        bytes=stats.bytes,
                        modified_at=_mtime(stats.newest_mtime),
                        sha256=None,
                    )
                )
            elif stat.S_ISREG(st.st_mode):
                files.append(
                    FileEntry(
                        name=name,
                        type=EntryType.FILE,
                        bytes=st.st_size,
                        modified_at=_mtime(st.st_mtime),
                        sha256=sha256_file(path),
                    )
                )
            else:
                logger.warning("listing site=%s: skipping %r, not a regular file or a folder", slug, name)
        folders.sort(key=lambda e: e.name)
        files.sort(key=lambda e: e.name)
        return FileListing(site=slug, path=folder, entries=folders + files)

    @staticmethod
    def _scan(directory: Path) -> Iterator[tuple[str, Path, os.stat_result]]:
        with os.scandir(directory) as entries:
            for entry in entries:
                try:
                    st = entry.stat(follow_symlinks=False)
                except OSError:
                    continue
                yield entry.name, Path(entry.path), st

    # ---- files ----

    def file_path(self, slug: str, path: str) -> Path:
        """The on-disk file at `path`, for a download: 404 when missing, 409 `not_a_file` for a folder."""
        target = self.resolve(slug, path)
        kind = kind_of(target)
        if kind is Kind.FOLDER:
            raise ApiError(409, ErrorCode.NOT_A_FILE, f"{path!r} is a folder, not a file")
        if kind is Kind.SPECIAL:
            raise _invalid_path(path, "not a regular file")
        if kind is Kind.MISSING:
            raise ApiError(404, ErrorCode.NOT_FOUND, f"no file {path!r} in site {slug!r}")
        return target

    async def write_file(
        self,
        slug: str,
        path: str,
        body: AsyncIterator[bytes],
        *,
        overwrite: bool,
        declared_length: int | None,
        holder: str,
    ) -> UploadResult:
        """Store the streamed body at `path`, creating missing parent folders.

        Checks run in the contract's order: the path (400), the size cap against the declared
        length (413), then the conflicts (409) under the path's lock, which stays held until the
        new file is in place. The cap is enforced again on the bytes actually received.
        """
        target = self.resolve(slug, path)
        if declared_length is not None and declared_length > self._max_upload_bytes:
            raise self._too_large()
        async with self.locks.hold(slug, path, holder):
            replaced = self._check_write_target(slug, target, path, overwrite=overwrite)
            digest, size = await self._stage_and_replace(target, body)
        return UploadResult(path=path, bytes=size, sha256=digest, replaced=replaced)

    def _check_write_target(self, slug: str, target: Path, path: str, *, overwrite: bool) -> bool:
        """Validate a write to `target`; return whether it replaces an existing file."""
        root = self.site_dir(slug)
        parent = target.parent
        while parent != root:
            parent_kind = kind_of(parent)
            if parent_kind is Kind.FILE:
                raise ApiError(
                    409, ErrorCode.NOT_A_FOLDER, f"{self._relative(slug, parent)!r} is a file, not a folder"
                )
            if parent_kind is Kind.SPECIAL:
                raise _invalid_path(path, f"{self._relative(slug, parent)!r} is not a folder")
            parent = parent.parent
        kind = kind_of(target)
        if kind is Kind.FOLDER:
            raise ApiError(409, ErrorCode.NOT_A_FILE, f"{path!r} is a folder")
        if kind is Kind.SPECIAL:
            raise _invalid_path(path, "not a regular file")
        if kind is Kind.FILE:
            if not overwrite:
                raise ApiError(
                    409, ErrorCode.FILE_EXISTS, f"{path!r} exists; pass overwrite=true to replace it"
                )
            return True
        return False

    async def _stage_and_replace(self, target: Path, body: AsyncIterator[bytes]) -> tuple[str, int]:
        staging = self._staging_dir / f"{secrets.token_hex(8)}.part"
        digest = hashlib.sha256()
        size = 0
        try:
            with open_for_writing(staging) as out:
                async for chunk in body:
                    if not chunk:
                        continue
                    size += len(chunk)
                    if size > self._max_upload_bytes:
                        raise self._too_large()
                    digest.update(chunk)
                    out.write(chunk)
                out.flush()
                await asyncio.to_thread(os.fsync, out.fileno())
            target.parent.mkdir(parents=True, exist_ok=True)
            os.replace(staging, target)
        except OSError as e:
            _unlink(staging)
            if e.errno in _STORAGE_FULL:
                raise ApiError(
                    507,
                    ErrorCode.INSUFFICIENT_STORAGE,
                    "the disk is full or the quota is reached; nothing was written",
                ) from e
            raise
        except BaseException:
            _unlink(staging)
            raise
        return digest.hexdigest(), size

    def _too_large(self) -> ApiError:
        return ApiError(
            413,
            ErrorCode.PAYLOAD_TOO_LARGE,
            f"the file exceeds the cap of {self._max_upload_bytes} bytes"
            f" ({self._max_upload_bytes // (1024 * 1024)} MiB)",
        )

    async def delete_file(self, slug: str, path: str, *, holder: str) -> None:
        target = self.resolve(slug, path)
        async with self.locks.hold(slug, path, holder):
            kind = kind_of(target)
            if kind is Kind.FOLDER:
                raise ApiError(409, ErrorCode.NOT_A_FILE, f"{path!r} is a folder; use the folders route")
            if kind is Kind.SPECIAL:
                raise _invalid_path(path, "not a regular file")
            if kind is Kind.MISSING:
                raise ApiError(404, ErrorCode.NOT_FOUND, f"no file {path!r} in site {slug!r}")
            target.unlink()

    async def delete_folder(self, slug: str, path: str, *, recursive: bool) -> None:
        target = self.resolve(slug, path)
        kind = kind_of(target)
        if kind is Kind.FILE:
            raise ApiError(409, ErrorCode.NOT_A_FOLDER, f"{path!r} is a file; use the files route")
        if kind is Kind.SPECIAL:
            raise _invalid_path(path, "not a folder")
        if kind is Kind.MISSING:
            raise ApiError(404, ErrorCode.NOT_FOUND, f"no folder {path!r} in site {slug!r}")
        with os.scandir(target) as entries:
            empty = next(iter(entries), None) is None
        if not empty and not recursive:
            raise ApiError(
                409,
                ErrorCode.FOLDER_NOT_EMPTY,
                f"{path!r} is not empty; pass recursive=true to delete everything in it",
            )
        await asyncio.to_thread(shutil.rmtree, target)
