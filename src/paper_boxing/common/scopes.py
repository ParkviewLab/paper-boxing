# SPDX-FileCopyrightText: 2026 Gary Frattarola <garyf@parkviewlab.ai>
#
# SPDX-License-Identifier: MIT OR Apache-2.0

"""Token types and permission tiers, shared by the backend (which enforces
them), the frontend (which offers them when a token is created) and the MCP
server (which lists and refuses tools by them).

Tier order: READ_ONLY (0) < READ_WRITE (1) < REMOVE_DESTRUCTIVE (2). A holder
at tier N may do anything whose required scope is at tier N or below.
"""

from __future__ import annotations

from enum import StrEnum


class Scope(StrEnum):
    """The handbook's permission tiers, here as the scope of an access token."""

    READ_ONLY = "read_only"
    READ_WRITE = "read_write"
    REMOVE_DESTRUCTIVE = "remove_destructive"


class TokenType(StrEnum):
    """`session`: issued at sign-in, for the UI. `agent`: created by a user, for agents (the only type the MCP server accepts)."""

    SESSION = "session"
    AGENT = "agent"


SCOPE_TIER: dict[Scope, int] = {
    Scope.READ_ONLY: 0,
    Scope.READ_WRITE: 1,
    Scope.REMOVE_DESTRUCTIVE: 2,
}

# A session token acts with the full rights of its user; there are no roles.
SESSION_SCOPE = Scope.REMOVE_DESTRUCTIVE


def scope_allows(held: Scope, required: Scope) -> bool:
    """True when a holder of `held` may perform something that requires `required`."""
    return SCOPE_TIER[held] >= SCOPE_TIER[required]
