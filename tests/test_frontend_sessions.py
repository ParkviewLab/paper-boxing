# SPDX-FileCopyrightText: 2026 Gary Frattarola <garyf@parkviewlab.ai>
#
# SPDX-License-Identifier: MIT OR Apache-2.0

"""Several simultaneous sessions (docs/design.md section 5): two people signed
in at the same time never see each other's session, whilst the sites and the
agent tokens, which the contract makes global, are seen by both."""

from __future__ import annotations

from nicegui.testing import User

from paper_boxing.common.fake_backend import FakeState
from paper_boxing.common.scopes import TokenType
from tests import frontend_support as support


async def test_two_people_at_once(user: User, second_user: User, fake_state: FakeState) -> None:
    fake_state.add_user("second", "second-password")
    await support.sign_in(user, *support.ADMIN)
    await support.sign_in(second_user, "second", "second-password")

    assert user.find(marker="current-user").elements.pop().text == "admin"
    assert second_user.find(marker="current-user").elements.pop().text == "second"
    admin_sessions = support.session_secrets(fake_state, "admin")
    second_sessions = support.session_secrets(fake_state, "second")
    assert len(admin_sessions) == 1 and len(second_sessions) == 1

    # a site created by one is a site of the lab, listed to both
    support.act(user).find(marker="site-name").type("Shared")
    user.find(marker="create-site").click()
    await user.should_see("Site 'Shared' created")
    await second_user.open("/")
    await second_user.should_see("Shared")

    # a token created by one is listed to both, with its owner; equal rights let the other revoke it
    await user.open("/tokens")
    support.act(user).find(marker="token-name").type("admin's agent")
    user.find(marker="create-token").click()
    await user.should_see("Token 'admin's agent' created")
    user.find(marker="secret-done").click()
    await second_user.open("/tokens")
    await second_user.should_see("admin's agent")
    await second_user.should_see("owner admin")
    support.act(second_user).find(marker="revoke-token").click()
    await second_user.should_see(marker="confirm-revoke")
    second_user.find(marker="confirm-revoke").click()
    await second_user.should_see("Token 'admin's agent' revoked")
    assert all(t.revoked for t in fake_state.tokens.values() if t.type is TokenType.AGENT)

    # neither page ever carries a session token, its own or the other's
    for path in ("/", "/tokens", "/users", "/account"):
        await user.open(path)
        await second_user.open(path)
        for secret in admin_sessions + second_sessions:
            assert secret not in support.page_text(user), path
            assert secret not in support.page_text(second_user), path

    # signing one out leaves the other signed in
    support.act(user).find("Sign out").click()
    await user.should_see(marker="sign-in")
    await second_user.open("/account")
    await second_user.should_see("Signed in as second")
    assert support.session_secrets(fake_state, "admin") == []
    assert support.session_secrets(fake_state, "second") == second_sessions


async def test_a_second_browser_is_not_signed_in_by_the_first(user: User, second_user: User) -> None:
    await support.sign_in(user, *support.ADMIN)
    await second_user.open("/")
    await second_user.should_see(marker="sign-in")
    await second_user.should_not_see(marker="current-user")


async def test_each_request_carries_its_own_session(
    user: User, second_user: User, fake_state: FakeState
) -> None:
    fake_state.add_user("second", "second-password")
    await support.sign_in(user, *support.ADMIN)
    await support.sign_in(second_user, "second", "second-password")
    (admin_session,) = support.session_secrets(fake_state, "admin")
    (second_session,) = support.session_secrets(fake_state, "second")
    admin_id, second_id = fake_state.secrets[admin_session], fake_state.secrets[second_session]
    before = len(fake_state.requests)
    await user.open("/tokens")
    await second_user.open("/users")
    await user.open("/users")
    routes = [(r.route, r.token_id) for r in fake_state.requests[before:]]
    assert routes == [("list_tokens", admin_id), ("list_users", second_id), ("list_users", admin_id)]
