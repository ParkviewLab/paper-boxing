# SPDX-FileCopyrightText: 2026 Gary Frattarola <garyf@parkviewlab.ai>
#
# SPDX-License-Identifier: MIT OR Apache-2.0

"""Signing in: the redirect of an unauthenticated request to `/login`, the
return to the requested page afterwards, wrong credentials, and signing out."""

from __future__ import annotations

from nicegui.testing import User

from paper_boxing.common.fake_backend import FakeState
from tests import frontend_support as support


async def test_unauthenticated_request_lands_on_login(user: User) -> None:
    await user.open("/")
    await user.should_see(marker="sign-in")
    await user.should_not_see(marker="current-user")


async def test_sign_in_returns_to_the_requested_page(user: User, fake_state: FakeState) -> None:
    await user.open("/tokens")
    await user.should_see(marker="sign-in")
    user.find(marker="username").type(support.ADMIN[0])
    user.find(marker="password").type(support.ADMIN[1])
    user.find(marker="sign-in").click()
    await user.should_see("Agent tokens")
    assert user.find(marker="current-user").elements.pop().text == support.ADMIN[0]
    # the session was issued by the backend, for this user
    assert len(support.session_secrets(fake_state, support.ADMIN[0])) == 1


async def test_next_off_site_is_ignored(user: User) -> None:
    await support.sign_in(user, *support.ADMIN, at="/login?next=https%3A%2F%2Felsewhere.test%2F")
    await user.should_see("Sites")
    assert user.back_history[-1] == "/"


async def test_wrong_password_shows_the_backend_message(user: User, fake_state: FakeState) -> None:
    await user.open("/login")
    user.find(marker="username").type(support.ADMIN[0])
    user.find(marker="password").type("not-it")
    user.find(marker="sign-in").click()
    await user.should_see("unknown username or wrong password")
    await user.should_not_see(marker="current-user")
    assert support.session_secrets(fake_state, support.ADMIN[0]) == []


async def test_signed_in_visitor_of_login_is_sent_on(user: User) -> None:
    await support.sign_in(user, *support.ADMIN)
    await user.open("/login?next=%2Fusers")
    await user.should_see("Add an account")


async def test_sign_out_ends_the_session(user: User, fake_state: FakeState) -> None:
    await support.sign_in(user, *support.ADMIN)
    assert len(support.session_secrets(fake_state, support.ADMIN[0])) == 1
    user.find("Sign out").click()
    await user.should_see(marker="sign-in")
    assert support.session_secrets(fake_state, support.ADMIN[0]) == []
    await user.open("/")
    await user.should_see(marker="sign-in")
