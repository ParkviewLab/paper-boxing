# SPDX-FileCopyrightText: 2026 Gary Frattarola <garyf@parkviewlab.ai>
#
# SPDX-License-Identifier: MIT OR Apache-2.0

"""The contract's models: enums serialise as strings, validation rules hold, bodies round-trip."""

from __future__ import annotations

import json
from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from paper_boxing.common.schema import (
    ChangePasswordRequest,
    CreateUserRequest,
    ErrorBody,
    ErrorCode,
    Token,
)
from paper_boxing.common.scopes import SCOPE_TIER, Scope, TokenType, scope_allows


def test_error_body_round_trip() -> None:
    raw = '{"error": {"code": "file_exists", "message": "x"}}'
    body = ErrorBody.model_validate_json(raw)
    assert body.error.code is ErrorCode.FILE_EXISTS
    assert json.loads(body.model_dump_json()) == json.loads(raw)


def test_unknown_error_code_is_rejected() -> None:
    with pytest.raises(ValidationError):
        ErrorBody.model_validate({"error": {"code": "made_up", "message": "x"}})


def test_token_serialises_enums_as_strings() -> None:
    token = Token(
        id="tok_1",
        name="n",
        type=TokenType.AGENT,
        scope=Scope.READ_WRITE,
        username="u",
        created_at=datetime(2026, 1, 1, tzinfo=UTC),
        last_used_at=None,
        expires_at=None,
    )
    dumped = token.model_dump(mode="json")
    assert dumped["type"] == "agent"
    assert dumped["scope"] == "read_write"
    assert dumped["created_at"].startswith("2026-01-01T00:00:00")


@pytest.mark.parametrize("username", ["a", "gary", "g.f-1_x", "A9"])
def test_valid_usernames(username: str) -> None:
    assert CreateUserRequest(username=username, password="12345678").username == username


@pytest.mark.parametrize("username", ["", " gary", "-x", ".x", "gary!", "a" * 65, "a b"])
def test_invalid_usernames(username: str) -> None:
    with pytest.raises(ValidationError):
        CreateUserRequest(username=username, password="12345678")


def test_password_minimum_length() -> None:
    with pytest.raises(ValidationError):
        CreateUserRequest(username="u", password="1234567")
    with pytest.raises(ValidationError):
        ChangePasswordRequest(current="x", new="short")


def test_scope_tiers() -> None:
    assert SCOPE_TIER[Scope.READ_ONLY] < SCOPE_TIER[Scope.READ_WRITE] < SCOPE_TIER[Scope.REMOVE_DESTRUCTIVE]
    assert scope_allows(Scope.REMOVE_DESTRUCTIVE, Scope.READ_ONLY)
    assert not scope_allows(Scope.READ_ONLY, Scope.READ_WRITE)
    assert scope_allows(Scope.READ_WRITE, Scope.READ_WRITE)
