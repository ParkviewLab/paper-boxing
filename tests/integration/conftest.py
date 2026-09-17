# SPDX-FileCopyrightText: 2026 Gary Frattarola <garyf@parkviewlab.ai>
#
# SPDX-License-Identifier: MIT OR Apache-2.0

"""The integration tier: end-to-end tests against the running compose stack.

Run them with the stack up (see compose.build.yml). With
`PAPER_BOXING_INTEGRATION=1` set, as the CI job does, an unreachable stack
fails; without it, the tier is skipped, so a plain `uv run pytest` on a
machine with no stack still passes.
"""

from __future__ import annotations

import os

import httpx
import pytest

HOST = os.environ.get("PAPER_BOXING_INTEGRATION_HOST", "127.0.0.1")
FRONTEND = f"http://{HOST}:35840"
SITES = f"http://{HOST}:35841"
MCP = f"http://{HOST}:35842"
BACKEND = f"http://{HOST}:35843"
REQUIRED = os.environ.get("PAPER_BOXING_INTEGRATION") == "1"


def _stack_answers() -> bool:
    try:
        return httpx.get(f"{BACKEND}/health", timeout=2.0).status_code == 200
    except httpx.HTTPError:
        return False


@pytest.fixture(scope="session", autouse=True)
def stack() -> None:
    if not _stack_answers():
        if REQUIRED:
            pytest.fail(f"PAPER_BOXING_INTEGRATION=1 but the stack does not answer at {BACKEND}/health")
        pytest.skip(f"no paper-boxing stack at {BACKEND}; start it with tests/integration/compose.build.yml")
