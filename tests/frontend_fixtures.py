# SPDX-FileCopyrightText: 2026 Gary Frattarola <garyf@parkviewlab.ai>
#
# SPDX-License-Identifier: MIT OR Apache-2.0

"""The frontend's pytest fixtures, loaded as a plugin from `tests/conftest.py`.

They build on `nicegui.testing`'s `user` fixture (`user_plugin`, not `plugin`,
which imports selenium), which executes `tests/frontend_main.py` afresh per
test; that file installs the frontend against a fake backend and leaves the
fake's state in `tests.frontend_support`. Kept out of `conftest.py` so that
the frontend adds nothing to that file's import block and no fixture name
there is shared with the MCP suite's (`fake_state`, `fake_backend_app`,
`mcp_client`, `ADMIN`).
"""

from __future__ import annotations

from collections.abc import AsyncIterator

import pytest
from nicegui.testing import User

from paper_boxing.common.fake_backend import FakeState
from tests import frontend_support as support


@pytest.fixture
def frontend_state(user: User) -> FakeState:
    """The fake backend behind the frontend under test (built by tests/frontend_main.py for this test)."""
    return support.current().state


@pytest.fixture
async def second_user(user: User) -> AsyncIterator[User]:
    """Another person in another browser: its own cookie jar, so its own `app.storage.user`."""
    other = support.new_user()
    yield other
    await other.http_client.aclose()
