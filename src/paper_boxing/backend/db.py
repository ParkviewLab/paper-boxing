# SPDX-FileCopyrightText: 2026 Gary Frattarola <garyf@parkviewlab.ai>
#
# SPDX-License-Identifier: MIT OR Apache-2.0

"""SQLite storage of users, sessions, agent tokens and site metadata
(docs/design.md section 4): `paper-boxing.sqlite3` in the data directory.

The standard library's `sqlite3` in WAL mode, one connection, every access
under a re-entrant lock so the connection can be shared between the event
loop and worker threads. No secret enters this module: a token arrives as
its sha256 hex digest and a password as its argon2id encoded hash, and the
rows never hold anything else.

Sessions and agent tokens share one table, told apart by `type`; a revoked
token keeps its row with `revoked_at` set (so a second revocation is a 404
rather than a silent success), and a removed user's tokens go with the user
through the foreign key. Timestamps are ISO 8601 UTC strings.
"""

from __future__ import annotations

import logging
import sqlite3
import threading
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from paper_boxing.common.scopes import Scope, TokenType

logger = logging.getLogger(__name__)

SCHEMA_VERSION = 1

_SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
    id            TEXT PRIMARY KEY,
    username      TEXT NOT NULL UNIQUE,
    password_hash TEXT NOT NULL,
    created_at    TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS tokens (
    id            TEXT PRIMARY KEY,
    secret_sha256 TEXT NOT NULL UNIQUE,
    name          TEXT NOT NULL,
    type          TEXT NOT NULL CHECK (type IN ('session', 'agent')),
    scope         TEXT NOT NULL CHECK (scope IN ('read_only', 'read_write', 'remove_destructive')),
    user_id       TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    created_at    TEXT NOT NULL,
    last_used_at  TEXT,
    expires_at    TEXT,
    revoked_at    TEXT
);
CREATE INDEX IF NOT EXISTS tokens_by_user ON tokens(user_id);
CREATE TABLE IF NOT EXISTS sites (
    slug       TEXT PRIMARY KEY,
    name       TEXT NOT NULL,
    created_at TEXT NOT NULL
);
"""


class Conflict(Exception):
    """A row with the same unique key exists (username, site slug)."""


@dataclass(frozen=True)
class UserRow:
    id: str
    username: str
    password_hash: str
    created_at: datetime


@dataclass(frozen=True)
class TokenRow:
    id: str
    name: str
    type: TokenType
    scope: Scope
    user_id: str
    created_at: datetime
    last_used_at: datetime | None
    expires_at: datetime | None
    revoked_at: datetime | None


@dataclass(frozen=True)
class SiteRow:
    slug: str
    name: str
    created_at: datetime


def _iso(value: datetime | None) -> str | None:
    return None if value is None else value.astimezone(UTC).isoformat()


def _dt(value: str | None) -> datetime | None:
    return None if value is None else datetime.fromisoformat(value)


def _user(row: sqlite3.Row) -> UserRow:
    created = _dt(row["created_at"])
    assert created is not None
    return UserRow(
        id=row["id"], username=row["username"], password_hash=row["password_hash"], created_at=created
    )


def _token(row: sqlite3.Row) -> TokenRow:
    created = _dt(row["created_at"])
    assert created is not None
    return TokenRow(
        id=row["id"],
        name=row["name"],
        type=TokenType(row["type"]),
        scope=Scope(row["scope"]),
        user_id=row["user_id"],
        created_at=created,
        last_used_at=_dt(row["last_used_at"]),
        expires_at=_dt(row["expires_at"]),
        revoked_at=_dt(row["revoked_at"]),
    )


def _site(row: sqlite3.Row) -> SiteRow:
    created = _dt(row["created_at"])
    assert created is not None
    return SiteRow(slug=row["slug"], name=row["name"], created_at=created)


class Database:
    """The one connection to `paper-boxing.sqlite3`; `open()` creates the file and the schema."""

    def __init__(self, connection: sqlite3.Connection, path: Path) -> None:
        self._conn = connection
        self._path = path
        self._lock = threading.RLock()

    @classmethod
    def open(cls, path: Path) -> Database:
        path.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(path, check_same_thread=False, timeout=5.0)
        conn.row_factory = sqlite3.Row
        mode = conn.execute("PRAGMA journal_mode=WAL").fetchone()[0]
        if mode != "wal":
            logger.warning("SQLite journal mode is %r, not WAL (unsupported filesystem?)", mode)
        conn.execute("PRAGMA synchronous=NORMAL")
        conn.execute("PRAGMA foreign_keys=ON")
        conn.executescript(_SCHEMA)
        conn.execute(f"PRAGMA user_version={SCHEMA_VERSION}")
        conn.commit()
        return cls(conn, path)

    @property
    def path(self) -> Path:
        return self._path

    def close(self) -> None:
        with self._lock:
            self._conn.close()

    @contextmanager
    def transaction(self) -> Iterator[sqlite3.Connection]:
        """Run a block against the connection under the lock; commit on success, roll back on any error."""
        with self._lock:
            try:
                yield self._conn
                self._conn.commit()
            except BaseException:
                self._conn.rollback()
                raise

    # ---- users ----

    def count_users(self) -> int:
        with self.transaction() as conn:
            return conn.execute("SELECT COUNT(*) FROM users").fetchone()[0]

    def insert_user(self, user: UserRow) -> None:
        with self.transaction() as conn:
            try:
                conn.execute(
                    "INSERT INTO users (id, username, password_hash, created_at) VALUES (?, ?, ?, ?)",
                    (user.id, user.username, user.password_hash, _iso(user.created_at)),
                )
            except sqlite3.IntegrityError as e:
                raise Conflict(f"the username {user.username!r} is taken") from e

    def user_by_id(self, user_id: str) -> UserRow | None:
        with self.transaction() as conn:
            row = conn.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()
        return None if row is None else _user(row)

    def user_by_username(self, username: str) -> UserRow | None:
        with self.transaction() as conn:
            row = conn.execute("SELECT * FROM users WHERE username = ?", (username,)).fetchone()
        return None if row is None else _user(row)

    def list_users(self) -> list[UserRow]:
        with self.transaction() as conn:
            rows = conn.execute("SELECT * FROM users ORDER BY username").fetchall()
        return [_user(r) for r in rows]

    def set_password_hash(self, user_id: str, password_hash: str) -> None:
        with self.transaction() as conn:
            conn.execute("UPDATE users SET password_hash = ? WHERE id = ?", (password_hash, user_id))

    def delete_user(self, user_id: str) -> bool:
        """Remove the account and, through the foreign key, every token it owned."""
        with self.transaction() as conn:
            cursor = conn.execute("DELETE FROM users WHERE id = ?", (user_id,))
            return cursor.rowcount > 0

    # ---- tokens ----

    def insert_token(self, token: TokenRow, secret_sha256: str) -> None:
        with self.transaction() as conn:
            conn.execute(
                "INSERT INTO tokens (id, secret_sha256, name, type, scope, user_id, created_at,"
                " last_used_at, expires_at, revoked_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    token.id,
                    secret_sha256,
                    token.name,
                    token.type.value,
                    token.scope.value,
                    token.user_id,
                    _iso(token.created_at),
                    _iso(token.last_used_at),
                    _iso(token.expires_at),
                    _iso(token.revoked_at),
                ),
            )

    def token_by_secret(self, secret_sha256: str) -> tuple[TokenRow, str] | None:
        """The token stored under a digest, with the stored digest so the caller can compare in constant time."""
        with self.transaction() as conn:
            row = conn.execute("SELECT * FROM tokens WHERE secret_sha256 = ?", (secret_sha256,)).fetchone()
        return None if row is None else (_token(row), row["secret_sha256"])

    def token_by_id(self, token_id: str) -> TokenRow | None:
        with self.transaction() as conn:
            row = conn.execute("SELECT * FROM tokens WHERE id = ?", (token_id,)).fetchone()
        return None if row is None else _token(row)

    def touch_token(self, token_id: str, *, last_used_at: datetime, expires_at: datetime | None) -> None:
        """Record a use; a session's expiry slides forward, an agent token's stays null."""
        with self.transaction() as conn:
            if expires_at is None:
                conn.execute(
                    "UPDATE tokens SET last_used_at = ? WHERE id = ?", (_iso(last_used_at), token_id)
                )
            else:
                conn.execute(
                    "UPDATE tokens SET last_used_at = ?, expires_at = ? WHERE id = ?",
                    (_iso(last_used_at), _iso(expires_at), token_id),
                )

    def revoke_token(self, token_id: str, at: datetime) -> None:
        with self.transaction() as conn:
            conn.execute(
                "UPDATE tokens SET revoked_at = ? WHERE id = ? AND revoked_at IS NULL", (_iso(at), token_id)
            )

    def revoke_other_sessions(self, user_id: str, *, keep: str, at: datetime) -> int:
        with self.transaction() as conn:
            cursor = conn.execute(
                "UPDATE tokens SET revoked_at = ? WHERE user_id = ? AND type = 'session'"
                " AND id != ? AND revoked_at IS NULL",
                (_iso(at), user_id, keep),
            )
            return cursor.rowcount

    def list_agent_tokens(self) -> list[tuple[TokenRow, str]]:
        """Every live agent token with its owner's username, oldest first."""
        with self.transaction() as conn:
            rows = conn.execute(
                "SELECT tokens.*, users.username AS username FROM tokens JOIN users ON users.id = tokens.user_id"
                " WHERE tokens.type = 'agent' AND tokens.revoked_at IS NULL"
                " ORDER BY tokens.created_at, tokens.id"
            ).fetchall()
        return [(_token(r), r["username"]) for r in rows]

    def delete_dead_sessions(self, now: datetime) -> int:
        """Housekeeping: drop sessions that are revoked or expired; nothing can present them again."""
        with self.transaction() as conn:
            cursor = conn.execute(
                "DELETE FROM tokens WHERE type = 'session' AND (revoked_at IS NOT NULL OR expires_at <= ?)",
                (_iso(now),),
            )
            return cursor.rowcount

    # ---- sites ----

    def insert_site(self, site: SiteRow) -> None:
        with self.transaction() as conn:
            try:
                conn.execute(
                    "INSERT INTO sites (slug, name, created_at) VALUES (?, ?, ?)",
                    (site.slug, site.name, _iso(site.created_at)),
                )
            except sqlite3.IntegrityError as e:
                raise Conflict(f"a site with the slug {site.slug!r} exists") from e

    def site_by_slug(self, slug: str) -> SiteRow | None:
        with self.transaction() as conn:
            row = conn.execute("SELECT * FROM sites WHERE slug = ?", (slug,)).fetchone()
        return None if row is None else _site(row)

    def list_sites(self) -> list[SiteRow]:
        with self.transaction() as conn:
            rows = conn.execute("SELECT * FROM sites ORDER BY slug").fetchall()
        return [_site(r) for r in rows]

    def delete_site(self, slug: str) -> bool:
        with self.transaction() as conn:
            cursor = conn.execute("DELETE FROM sites WHERE slug = ?", (slug,))
            return cursor.rowcount > 0
