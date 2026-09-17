# SPDX-FileCopyrightText: 2026 Gary Frattarola <garyf@parkviewlab.ai>
#
# SPDX-License-Identifier: MIT OR Apache-2.0

"""Agent tokens: the secret shown exactly once, the list without secrets,
revocation, and the session token never shown."""

from __future__ import annotations

from nicegui.testing import User

from paper_boxing.common.fake_backend import FakeState
from paper_boxing.common.scopes import Scope, TokenType
from tests import frontend_support as support


def _agent_secrets(state: FakeState) -> list[str]:
    return [s for s, tid in state.secrets.items() if state.tokens[tid].type is TokenType.AGENT]


async def test_secret_is_shown_exactly_once(user: User, frontend_state: FakeState) -> None:
    await support.sign_in(user, *support.ADMIN, at="/tokens")
    await user.should_see(marker="no-tokens")
    user.find(marker="token-name").type("claude on the laptop")
    user.find("Read and write: also create sites and upload files").click()
    user.find(marker="create-token").click()
    await user.should_see("Token 'claude on the laptop' created")
    (secret,) = _agent_secrets(frontend_state)
    assert secret.startswith("pb_")
    assert user.find(marker="token-secret").elements.pop().value == secret
    assert frontend_state.tokens[frontend_state.secrets[secret]].scope is Scope.READ_WRITE
    user.find(marker="secret-done").click()
    await user.should_not_see(marker="token-secret")
    assert secret not in support.page_text(user)
    await user.should_see("claude on the laptop")
    await user.should_see("read_write")
    await user.should_see(f"owner {support.ADMIN[0]}")

    await user.open("/tokens")
    await user.should_see("claude on the laptop")
    assert secret not in support.page_text(user)


async def test_session_token_is_never_shown(user: User, frontend_state: FakeState) -> None:
    await support.sign_in(user, *support.ADMIN, at="/tokens")
    user.find(marker="token-name").type("t")
    user.find(marker="create-token").click()
    await user.should_see("Token 't' created")
    (session,) = support.session_secrets(frontend_state, support.ADMIN[0])
    for path in ("/tokens", "/", "/users", "/account"):
        await user.open(path)
        assert session not in support.page_text(user), path


async def test_revoke(user: User, frontend_state: FakeState) -> None:
    token, secret = frontend_state.issue_agent_token(support.ADMIN[0], "old agent", Scope.READ_ONLY)
    await support.sign_in(user, *support.ADMIN, at="/tokens")
    await user.should_see("old agent")
    assert secret not in support.page_text(user)
    user.find(marker="revoke-token").click()
    await user.should_see(marker="confirm-revoke")
    user.find(marker="confirm-revoke").click()
    await user.should_see("Token 'old agent' revoked")
    await user.should_see(marker="no-tokens")
    assert frontend_state.tokens[token.id].revoked is True


async def test_scope_is_explained_and_read_only_is_the_default(user: User, frontend_state: FakeState) -> None:
    await support.sign_in(user, *support.ADMIN, at="/tokens")
    await user.should_see("Read only: list sites and download files")
    await user.should_see("Remove and destructive: also delete files, folders and sites")
    user.find(marker="token-name").type("default")
    user.find(marker="create-token").click()
    await user.should_see("Token 'default' created")
    (secret,) = _agent_secrets(frontend_state)
    assert frontend_state.tokens[frontend_state.secrets[secret]].scope is Scope.READ_ONLY
