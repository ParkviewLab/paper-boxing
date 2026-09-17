# SPDX-FileCopyrightText: 2026 Gary Frattarola <garyf@parkviewlab.ai>
#
# SPDX-License-Identifier: MIT OR Apache-2.0

"""Shared by the frontend tests and their NiceGUI main file (`tests/frontend_main.py`).

The main file builds a fresh fake backend for every test and leaves it in
`harness`; the fixtures in `tests/conftest.py` hand it to the tests, together
with helpers that sign a simulated user in through the real login page.
"""

from __future__ import annotations

from dataclasses import dataclass

import httpx
from fastapi import FastAPI
from nicegui import core
from nicegui.testing import User

from paper_boxing.common.fake_backend import FakeState

ADMIN = ("admin", "admin-password")
PUBLIC_SITES_URL = "http://sites.test:35841"
STORAGE_SECRET = "test-storage-secret"


@dataclass
class Harness:
    app: FastAPI
    state: FakeState


harness: Harness | None = None


def current() -> Harness:
    if harness is None:
        raise RuntimeError("tests/frontend_main.py has not run; is the `user` fixture in use?")
    return harness


def new_user(*, cookies: httpx.Cookies | None = None) -> User:
    """A second simulated user (another browser); with `cookies`, another tab of the same browser."""
    http = httpx.AsyncClient(transport=httpx.ASGITransport(core.app), base_url="http://test", cookies=cookies)
    return User(http)


async def sign_in(user: User, username: str, password: str, *, at: str = "/") -> None:
    """Sign a simulated user in through the login page and wait for the requested page."""
    await user.open(at)
    await user.should_see(marker="sign-in")
    user.find(marker="username").type(username)
    user.find(marker="password").type(password)
    user.find(marker="sign-in").click()
    await user.should_see(marker="current-user")


def session_secrets(state: FakeState, username: str) -> list[str]:
    """Every live session secret of `username` in the fake backend."""
    user = state.user_by_name(username)
    return [
        secret
        for secret, token_id in state.secrets.items()
        if (token := state.tokens[token_id]).type == "session"
        and token.user_id == user.id
        and not token.revoked
    ]


def page_text(user: User) -> str:
    """Everything the user's page currently renders, for asserting what is not there."""
    return str(user.current_layout)
