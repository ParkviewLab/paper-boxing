# SPDX-FileCopyrightText: 2026 Gary Frattarola <garyf@parkviewlab.ai>
#
# SPDX-License-Identifier: MIT OR Apache-2.0

"""The MCP server while the backend is down: it answers `503
backend_unreachable` in the contract's shape and runs no tool, and once the
backend is back it recovers without a restart of its own. The backend
container is stopped and started with docker; the collection hook in
`conftest.py` runs this module last, and the backend is started again
whatever the outcome.
"""

from __future__ import annotations

import time

import httpx
import pytest

from tests.integration.conftest import (
    BACKEND,
    BACKEND_CONTAINER,
    MCP,
    MCP_HEADERS,
    MCP_INIT,
    TIMEOUT,
    AgentToken,
    docker,
    wait_for_health,
)

pytestmark = pytest.mark.integration


def test_mcp_answers_503_while_the_backend_is_down_and_recovers(agent_tokens: dict[str, AgentToken]) -> None:
    headers = {**MCP_HEADERS, "Authorization": f"Bearer {agent_tokens['read_only'].secret}"}
    before = httpx.post(f"{MCP}/mcp", json=MCP_INIT, headers=headers, timeout=TIMEOUT)
    assert before.status_code == 200, before.text

    docker("stop", "--time", "5", BACKEND_CONTAINER)
    try:
        down = httpx.post(f"{MCP}/mcp", json=MCP_INIT, headers=headers, timeout=TIMEOUT)
        assert down.status_code == 503, down.text
        assert down.json()["error"]["code"] == "backend_unreachable"
        assert httpx.get(f"{MCP}/health", timeout=TIMEOUT).json()["ok"] is True  # the server itself is up
    finally:
        docker("start", BACKEND_CONTAINER)
        wait_for_health(BACKEND, 60.0)

    deadline = time.monotonic() + 20.0
    while True:
        after = httpx.post(f"{MCP}/mcp", json=MCP_INIT, headers=headers, timeout=TIMEOUT)
        if after.status_code == 200 or time.monotonic() > deadline:
            break
        time.sleep(0.5)
    assert after.status_code == 200, after.text
    assert "paper-boxing-mcp" in after.text
