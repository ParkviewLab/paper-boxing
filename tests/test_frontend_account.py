# SPDX-FileCopyrightText: 2026 Gary Frattarola <garyf@parkviewlab.ai>
#
# SPDX-License-Identifier: MIT OR Apache-2.0

"""Changing one's own password: the checks on the page, the backend's refusal
of a wrong current password, and the effect on other sessions."""

from __future__ import annotations

from nicegui.testing import User

from paper_boxing.common.fake_backend import FakeState
from tests import frontend_support as support


async def test_change_password(user: User, fake_state: FakeState) -> None:
    other_session = fake_state.issue_session(support.ADMIN[0])  # another browser, signed in earlier
    await support.sign_in(user, *support.ADMIN, at="/account")
    await user.should_see(f"Signed in as {support.ADMIN[0]}")
    user.find(marker="current-password").type(support.ADMIN[1])
    user.find(marker="new-password").type("new-password-1")
    user.find(marker="new-password-again").type("new-password-1")
    user.find(marker="change-password").click()
    await user.should_see("Password changed; your other sessions are signed out.")
    assert fake_state.user_by_name(support.ADMIN[0]).password == "new-password-1"
    assert fake_state.tokens[fake_state.secrets[other_session]].revoked is True
    assert len(support.session_secrets(fake_state, support.ADMIN[0])) == 1  # this one survives
    await user.open("/")
    await user.should_see(marker="current-user")


async def test_wrong_current_password_is_refused(user: User, fake_state: FakeState) -> None:
    await support.sign_in(user, *support.ADMIN, at="/account")
    user.find(marker="current-password").type("not-it")
    user.find(marker="new-password").type("new-password-1")
    user.find(marker="new-password-again").type("new-password-1")
    user.find(marker="change-password").click()
    await user.should_see("the current password is wrong")
    assert fake_state.user_by_name(support.ADMIN[0]).password == support.ADMIN[1]


async def test_checks_on_the_page(user: User, fake_state: FakeState) -> None:
    await support.sign_in(user, *support.ADMIN, at="/account")
    user.find(marker="current-password").type(support.ADMIN[1])
    user.find(marker="new-password").type("short")
    user.find(marker="new-password-again").type("short")
    user.find(marker="change-password").click()
    await user.should_see("needs at least 8 characters")
    user.find(marker="new-password").clear()
    user.find(marker="new-password").type("long-enough-1")
    user.find(marker="new-password-again").clear()
    user.find(marker="new-password-again").type("long-enough-2")
    user.find(marker="change-password").click()
    await user.should_see("differ")
    assert fake_state.user_by_name(support.ADMIN[0]).password == support.ADMIN[1]
    assert not [r for r in fake_state.requests if r.route == "change_password"]
