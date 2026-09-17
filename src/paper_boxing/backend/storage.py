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
size cap and with no lock held; the staging name is per request. Only then
is the site's lock taken, for the short critical section that confirms the
site still exists, re-runs the containment and target checks, and moves the
file into place with `os.replace`, so a reader (nginx included) sees the old
file or the new one and never a partial one, and a failure of any kind
leaves the old file whole and the staging file removed. Every change to a
site's tree (a file landing, a file or folder removed, the site created or
removed) runs under that one per-site `asyncio.Lock`, so an upload whose
body arrived while a deletion ran cannot recreate what the deletion removed.
A disk-full or quota error is `507 insufficient_storage` with nothing
half-written.
"""

from __future__ import annotations

import asyncio
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

from paper_boxing.backend.db import Conflict, Database, SiteRow
from paper_boxing.common.errors import ApiError
from paper_boxing.common.schema import EntryType, ErrorCode, FileEntry, FileListing, UploadResult

logger = logging.getLogger(__name__)

READ_CHUNK = 1024 * 1024


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
    waiting: int = 0


class SiteLocks:
    """One `asyncio.Lock` per site, created on first use and dropped when nobody holds or awaits it."""

    def __init__(self) -> None:
        self._entries: dict[str, _LockEntry] = {}

    @asynccontextmanager
    async def hold(self, slug: str) -> AsyncIterator[None]:
        entry = self._entries.get(slug)
        if entry is None:
            entry = self._entries[slug] = _LockEntry(asyncio.Lock())
        entry.waiting += 1
        try:
            async with entry.lock:
                yield
        finally:
            entry.waiting -= 1
            if entry.waiting == 0 and self._entries.get(slug) is entry:
                del self._entries[slug]

    def is_locked(self, slug: str) -> bool:
        entry = self._entries.get(slug)
        return entry is not None and entry.lock.locked()

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


@dataclass(frozen=True)
class Staged:
    """A request body received whole into staging, with what was learnt while receiving it."""

    path: Path
    sha256: str
    bytes: int


class Storage:
    """The site trees under `sites_dir`, with `staging_dir` beside them on the same filesystem, and the
    site rows in `db` that say which trees are sites."""

    def __init__(self, sites_dir: Path, staging_dir: Path, *, max_upload_bytes: int, db: Database) -> None:
        self._sites_dir = sites_dir
        self._staging_dir = staging_dir
        self._max_upload_bytes = max_upload_bytes
        self._db = db
        self.locks = SiteLocks()

    @property
    def sites_dir(self) -> Path:
        return self._sites_dir

    @property
    def staging_dir(self) -> Path:
        return self._staging_dir

    @property
    def max_upload_bytes(self) -> int:
        return self._max_upload_bytes

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

    async def create_site(self, slug: str, name: str, created_at: datetime) -> SiteRow:
        """Create the site's folder, then its row. A folder already on the volume is never adopted:
        it is `409 site_exists`, and a row that fails to insert takes the new folder away with it."""
        async with self.locks.hold(slug):
            if self._db.site_by_slug(slug) is not None:
                raise ApiError(409, ErrorCode.SITE_EXISTS, f"a site with the slug {slug!r} exists")
            root = self.site_dir(slug)
            try:
                root.mkdir(parents=True, exist_ok=False)
            except FileExistsError as e:
                raise ApiError(
                    409,
                    ErrorCode.SITE_EXISTS,
                    f"a site with the slug {slug!r} exists: its directory is already on the volume",
                ) from e
            site = SiteRow(slug=slug, name=name, created_at=created_at)
            try:
                self._db.insert_site(site)
            except Conflict as e:
                with suppress(OSError):
                    root.rmdir()
                raise ApiError(409, ErrorCode.SITE_EXISTS, f"a site with the slug {slug!r} exists") from e
            except BaseException:
                with suppress(OSError):
                    root.rmdir()
                raise
        return site

    def site_totals(self, slug: str) -> tuple[int, int]:
        """(file count, bytes) of a site; (0, 0) when its folder is missing on disk."""
        root = self.site_dir(slug)
        if kind_of(root) is not Kind.FOLDER:
            return 0, 0
        stats = tree_stats(root)
        return stats.file_count, stats.bytes

    async def delete_site(self, slug: str) -> None:
        """Under the site's lock, move its folder into staging in one rename (so nginx stops serving it
        at once) and delete its row; then remove the parked folder."""
        parked: Path | None = None
        async with self.locks.hold(slug):
            root = self.site_dir(slug)
            if kind_of(root) is not Kind.MISSING:
                parked = self._staging_dir / f"{slug}.deleting-{secrets.token_hex(4)}"
                os.replace(root, parked)
            self._db.delete_site(slug)
        if parked is None:
            return
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
    ) -> UploadResult:
        """Store the streamed body at `path`, creating missing parent folders.

        The path (400) and the declared length against the cap (413) are checked before a byte
        is read; the body then streams into staging, the cap enforced on the bytes received, with
        no lock held. Under the site's lock the site row is confirmed to still exist (404 when the
        site was deleted meanwhile), the containment and target checks run again (400, 409), and
        the file moves into place. A failure anywhere removes the staging file.
        """
        self.resolve(slug, path)
        if declared_length is not None and declared_length > self._max_upload_bytes:
            raise self._too_large()
        staged = await self._stage(body)
        try:
            async with self.locks.hold(slug):
                if self._db.site_by_slug(slug) is None:
                    raise ApiError(
                        404,
                        ErrorCode.NOT_FOUND,
                        f"site {slug!r} was deleted while the file was being received",
                    )
                target = self.resolve(slug, path)
                replaced = self._check_write_target(slug, target, path, overwrite=overwrite)
                target.parent.mkdir(parents=True, exist_ok=True)
                os.replace(staged.path, target)
        except BaseException:
            _unlink(staged.path)
            raise
        return UploadResult(path=path, bytes=staged.bytes, sha256=staged.sha256, replaced=replaced)

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

    async def _stage(self, body: AsyncIterator[bytes]) -> Staged:
        """Receive the body into a fresh staging file, hashing as it arrives and enforcing the cap."""
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
        except BaseException:
            _unlink(staging)  # a full disk included: the app answers 507 once the staging file is gone
            raise
        return Staged(path=staging, sha256=digest.hexdigest(), bytes=size)

    def _too_large(self) -> ApiError:
        return ApiError(
            413,
            ErrorCode.PAYLOAD_TOO_LARGE,
            f"the file exceeds the cap of {self._max_upload_bytes} bytes"
            f" ({self._max_upload_bytes // (1024 * 1024)} MiB)",
        )

    async def delete_file(self, slug: str, path: str) -> None:
        async with self.locks.hold(slug):
            target = self.resolve(slug, path)
            kind = kind_of(target)
            if kind is Kind.FOLDER:
                raise ApiError(409, ErrorCode.NOT_A_FILE, f"{path!r} is a folder; use the folders route")
            if kind is Kind.SPECIAL:
                raise _invalid_path(path, "not a regular file")
            if kind is Kind.MISSING:
                raise ApiError(404, ErrorCode.NOT_FOUND, f"no file {path!r} in site {slug!r}")
            target.unlink()

    async def delete_folder(self, slug: str, path: str, *, recursive: bool) -> None:
        async with self.locks.hold(slug):
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
