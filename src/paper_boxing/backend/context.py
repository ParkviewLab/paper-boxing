# SPDX-FileCopyrightText: 2026 Gary Frattarola <garyf@parkviewlab.ai>
#
# SPDX-License-Identifier: MIT OR Apache-2.0

"""What a running backend holds, built in the app's lifespan and reachable
from a request as `request.app.state.backend`."""

from __future__ import annotations

from dataclasses import dataclass

from paper_boxing.backend.accounts import Accounts
from paper_boxing.backend.clock import Clock
from paper_boxing.backend.config import BackendConfig
from paper_boxing.backend.db import Database
from paper_boxing.backend.storage import Storage


@dataclass(frozen=True)
class Backend:
    config: BackendConfig
    clock: Clock
    db: Database
    accounts: Accounts
    storage: Storage
