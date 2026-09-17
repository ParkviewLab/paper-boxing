# SPDX-FileCopyrightText: 2026 Gary Frattarola <garyf@parkviewlab.ai>
#
# SPDX-License-Identifier: MIT OR Apache-2.0

"""Accounts, sessions and agent tokens: the authentication model of
docs/design.md section 4.

- A password is stored only as an argon2id hash (argon2-cffi: a random salt
  per hash, the cost settings inside the encoded string). At sign-in, a hash
  made under lower settings than the current ones is re-hashed.
- A token is 32 random bytes shown as `pb_` followed by 43 URL-safe base64
  characters. Only its sha256 digest is stored; a presented secret is hashed,
  looked up, and the stored digest compared in constant time. Every use is
  recorded.
- A session, issued at sign-in, expires after a sliding period; every
  authenticated request moves its expiry forward. An agent token does not
  expire: it lives until revoked, or until its owner's account is removed.

Nothing in this module logs or returns a password or a token secret; the
secret is returned to the caller exactly once, at creation.
"""

from __future__ import annotations

import contextlib
import hashlib
import hmac
import logging
import secrets
from dataclasses import dataclass, replace
from datetime import timedelta

import argon2
from argon2.exceptions import InvalidHashError, VerificationError
from pydantic import ValidationError

from paper_boxing.backend.clock import Clock
from paper_boxing.backend.db import Database, TokenRow, UserRow
from paper_boxing.common.schema import CreateUserRequest, Token, TokenSelf, User
from paper_boxing.common.scopes import SESSION_SCOPE, Scope, TokenType

logger = logging.getLogger(__name__)

TOKEN_PREFIX = "pb_"
SECRET_BYTES = 32  # 32 random bytes -> 43 URL-safe base64 characters without padding


def new_secret() -> str:
    """A fresh bearer secret: `pb_` and 43 URL-safe base64 characters from 32 random bytes."""
    return TOKEN_PREFIX + secrets.token_urlsafe(SECRET_BYTES)


def secret_digest(secret: str) -> str:
    """What the database holds for a secret: its lowercase hex sha256."""
    return hashlib.sha256(secret.encode("utf-8")).hexdigest()


def parse_bearer(header: str | None) -> str | None:
    """The secret in an `Authorization: Bearer <secret>` header, or None for a missing or malformed one."""
    if not header:
        return None
    scheme, _, value = header.partition(" ")
    value = value.strip()
    if scheme.lower() != "bearer" or not value:
        return None
    return value


def new_id(prefix: str) -> str:
    return f"{prefix}_{secrets.token_hex(6)}"


def default_hasher() -> argon2.PasswordHasher:
    """argon2id under argon2-cffi's current recommended parameters (RFC 9106's second profile)."""
    return argon2.PasswordHasher()


@dataclass(frozen=True)
class Actor:
    """Who acts on a request: the token presented and the user who owns it."""

    token: TokenRow
    user: UserRow

    def public_user(self) -> User:
        return public_user(self.user)


def public_user(user: UserRow) -> User:
    return User(id=user.id, username=user.username, created_at=user.created_at)


def public_token(token: TokenRow, username: str) -> Token:
    return Token(
        id=token.id,
        name=token.name,
        type=token.type,
        scope=token.scope,
        username=username,
        created_at=token.created_at,
        last_used_at=token.last_used_at,
        expires_at=token.expires_at,
    )


class Accounts:
    """Users, sign-in, sessions and agent tokens over the database, under one clock and one hasher."""

    def __init__(
        self,
        db: Database,
        clock: Clock,
        *,
        session_days: int,
        hasher: argon2.PasswordHasher | None = None,
    ) -> None:
        self._db = db
        self._clock = clock
        self._session_length = timedelta(days=session_days)
        self._hasher = hasher or default_hasher()
        # Verified against when the username is unknown, so that a sign-in takes the same time
        # whether or not the account exists.
        self._decoy_hash = self._hasher.hash(secrets.token_hex(16))

    # ---- users ----

    def bootstrap(self, username: str | None, password: str | None) -> UserRow | None:
        """Create the first account from the admin variables, only when no account exists yet.

        Once an account exists the variables are ignored. The pair is held to the same rules as
        `POST /users`; an invalid pair is a configuration error and raises, so the service does
        not start with an account it could never have created through the API.
        """
        if self._db.count_users() > 0:
            return None
        if username is None or password is None:
            logger.warning(
                "no account exists and PAPER_BOXING_ADMIN_USERNAME / PAPER_BOXING_ADMIN_PASSWORD are unset;"
                " nobody can sign in until they are set and the service restarted"
            )
            return None
        try:
            CreateUserRequest(username=username, password=password)
        except ValidationError as e:
            raise ValueError(
                "PAPER_BOXING_ADMIN_USERNAME must match [A-Za-z0-9][A-Za-z0-9._-]* (at most 64 characters)"
                " and PAPER_BOXING_ADMIN_PASSWORD must be at least 8 characters"
            ) from e
        user = self.create_user(username, password)
        logger.info("created the first account user=%s from the admin variables", username)
        return user

    def create_user(self, username: str, password: str) -> UserRow:
        """Add an account; raises `db.Conflict` when the username is taken."""
        user = UserRow(
            id=new_id("usr"),
            username=username,
            password_hash=self._hasher.hash(password),
            created_at=self._clock.now(),
        )
        self._db.insert_user(user)
        return user

    def verify_password(self, user: UserRow, password: str) -> bool:
        """Check a password; re-hash it when the stored hash's parameters are below the current ones."""
        try:
            self._hasher.verify(user.password_hash, password)
        except (VerificationError, InvalidHashError):
            return False
        if self._hasher.check_needs_rehash(user.password_hash):
            self._db.set_password_hash(user.id, self._hasher.hash(password))
            logger.info("re-hashed the password of user=%s under the current parameters", user.username)
        return True

    def change_password(self, user: UserRow, new_password: str) -> None:
        self._db.set_password_hash(user.id, self._hasher.hash(new_password))

    def delete_user(self, user_id: str) -> bool:
        """Remove an account and every token it owned. The last-account rule is the route's."""
        return self._db.delete_user(user_id)

    # ---- sessions ----

    def login(self, username: str, password: str) -> tuple[UserRow, TokenRow, str] | None:
        """Sign in: the user, the new session and its secret, or None for unknown or wrong credentials."""
        user = self._db.user_by_username(username)
        if user is None:
            with contextlib.suppress(VerificationError, InvalidHashError):
                self._hasher.verify(self._decoy_hash, password)
            return None
        if not self.verify_password(user, password):
            return None
        now = self._clock.now()
        session = TokenRow(
            id=new_id("tok"),
            name="session",
            type=TokenType.SESSION,
            scope=SESSION_SCOPE,
            user_id=user.id,
            created_at=now,
            last_used_at=None,
            expires_at=now + self._session_length,
            revoked_at=None,
        )
        secret = new_secret()
        self._db.insert_token(session, secret_digest(secret))
        return user, session, secret

    def logout(self, session: TokenRow) -> None:
        self._db.revoke_token(session.id, self._clock.now())

    def revoke_other_sessions(self, user: UserRow, *, keep: TokenRow) -> int:
        """Sign out the user's other sessions (after a password change); agent tokens are untouched."""
        return self._db.revoke_other_sessions(user.id, keep=keep.id, at=self._clock.now())

    def resolve_bearer(self, header: str | None) -> Actor | None:
        """The actor behind an Authorization header, or None when the token is missing, unknown,
        malformed, revoked or expired. A valid token has its use recorded and, for a session, its
        expiry moved forward.

        The lookup is by the secret's sha256 digest, which is what the table indexes; the stored
        digest is then compared with `hmac.compare_digest`, so the final decision does not depend on
        where a mismatch falls.
        """
        secret = parse_bearer(header)
        if secret is None:
            return None
        digest = secret_digest(secret)
        found = self._db.token_by_secret(digest)
        if found is None:
            return None
        token, stored = found
        if not hmac.compare_digest(stored, digest):
            return None
        now = self._clock.now()
        if token.revoked_at is not None or (token.expires_at is not None and token.expires_at <= now):
            return None
        user = self._db.user_by_id(token.user_id)
        if user is None:
            return None
        expires_at = now + self._session_length if token.type is TokenType.SESSION else None
        self._db.touch_token(token.id, last_used_at=now, expires_at=expires_at)
        return Actor(token=replace(token, last_used_at=now, expires_at=expires_at), user=user)

    def purge_dead_sessions(self) -> int:
        return self._db.delete_dead_sessions(self._clock.now())

    # ---- agent tokens ----

    def create_agent_token(self, owner: UserRow, name: str, scope: Scope) -> tuple[TokenRow, str]:
        """A new agent token owned by `owner`; returns the row and the secret, shown to the caller once."""
        token = TokenRow(
            id=new_id("tok"),
            name=name,
            type=TokenType.AGENT,
            scope=scope,
            user_id=owner.id,
            created_at=self._clock.now(),
            last_used_at=None,
            expires_at=None,
            revoked_at=None,
        )
        secret = new_secret()
        self._db.insert_token(token, secret_digest(secret))
        return token, secret

    def revoke_agent_token(self, token_id: str) -> bool:
        """Revoke a live agent token; False for an unknown, already revoked, or session token."""
        token = self._db.token_by_id(token_id)
        if token is None or token.type is not TokenType.AGENT or token.revoked_at is not None:
            return False
        self._db.revoke_token(token_id, self._clock.now())
        return True

    def list_agent_tokens(self) -> list[Token]:
        """Every live agent token of every user, with its owner's username, oldest first."""
        return [public_token(token, username) for token, username in self._db.list_agent_tokens()]

    def token_self(self, actor: Actor) -> TokenSelf:
        return TokenSelf(
            id=actor.token.id,
            name=actor.token.name,
            type=actor.token.type,
            scope=actor.token.scope,
            username=actor.user.username,
            expires_at=actor.token.expires_at,
        )
