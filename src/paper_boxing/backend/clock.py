# SPDX-FileCopyrightText: 2026 Gary Frattarola <garyf@parkviewlab.ai>
#
# SPDX-License-Identifier: MIT OR Apache-2.0

"""The backend's notion of now.

Every timestamp the backend writes (session expiry, last use, creation
times) comes from one `Clock`, so a test can move time forward with
`advance()` and watch a session expire; in production the offset is zero.
The interface matches the fake backend's clock, so the contract suite can
drive both with the same fixture.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta


class Clock:
    def __init__(self) -> None:
        self._offset = timedelta(0)

    def now(self) -> datetime:
        return datetime.now(UTC) + self._offset

    def advance(self, seconds: float) -> None:
        self._offset += timedelta(seconds=seconds)
