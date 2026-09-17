# SPDX-FileCopyrightText: 2026 Gary Frattarola <garyf@parkviewlab.ai>
#
# SPDX-License-Identifier: MIT OR Apache-2.0

"""Accounts: adding, the backend's validation messages, removal, the guard on
the last account, and removing one's own account."""

from __future__ import annotations

from nicegui.testing import User

from paper_boxing.common.fake_backend import FakeState
from tests import frontend_support as support


async def test_add_and_remove_an_account(user: User, frontend_state: FakeState) -> None:
    await support.sign_in(user, *support.ADMIN, at="/users")
    await user.should_see("Accounts")
    user.find(marker="new-username").type("second")
    user.find(marker="new-password").type("second-password")
    user.find(marker="add-user").click()
    await user.should_see("Account 'second' added")
    assert {u.username for u in frontend_state.users.values()} == {"admin", "second"}
    assert frontend_state.user_by_name("second").password == "second-password"

    assert len(user.find(marker="user-row").elements) == 2
    with user.scope(marker=f"user-{frontend_state.user_by_name('second').id}"):
        user.find(marker="remove-user").click()
    await user.should_see(marker="confirm-remove-user")
    user.find(marker="confirm-remove-user").click()
    await user.should_see("Account 'second' removed")
    assert {u.username for u in frontend_state.users.values()} == {"admin"}


async def test_validation_messages(user: User) -> None:
    await support.sign_in(user, *support.ADMIN, at="/users")
    user.find(marker="new-username").type("bad name")
    user.find(marker="new-password").type("long-enough")
    user.find(marker="add-user").click()
    await user.should_see("username")  # the 422 body names the field
    user.find(marker="new-username").clear()
    user.find(marker="new-username").type(support.ADMIN[0])
    user.find(marker="add-user").click()
    await user.should_see("the username 'admin' is taken")


async def test_the_last_account_cannot_be_removed(user: User, frontend_state: FakeState) -> None:
    await support.sign_in(user, *support.ADMIN, at="/users")
    await user.should_see("you")
    user.find(marker="remove-user").click()
    await user.should_see("This is your own account")
    user.find(marker="confirm-remove-user").click()
    await user.should_see("the last account cannot be deleted")
    assert len(frontend_state.users) == 1
    await user.should_see(marker="current-user")  # still signed in


async def test_removing_your_own_account_signs_you_out(user: User, frontend_state: FakeState) -> None:
    second = frontend_state.add_user("second", "second-password")
    await support.sign_in(user, "second", "second-password", at="/users")
    with user.scope(marker=f"user-{second.id}"):
        user.find(marker="remove-user").click()
    await user.should_see("This is your own account")
    user.find(marker="confirm-remove-user").click()
    await user.should_see(marker="sign-in")
    assert {u.username for u in frontend_state.users.values()} == {"admin"}
    assert support.session_secrets(frontend_state, support.ADMIN[0]) == []
