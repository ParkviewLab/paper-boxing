# SPDX-FileCopyrightText: 2026 Gary Frattarola <garyf@parkviewlab.ai>
#
# SPDX-License-Identifier: MIT OR Apache-2.0

"""The pages (docs/architecture.md, "The frontend"), one module each, and their route table.

Each module exposes one page builder, and `register()` binds it to its path
with `ui.page`, so a page is built afresh inside that function for every
client that opens it. Registration is a function rather than a decorator at
import time because NiceGUI's test harness resets the app between tests and
re-registers through `paper_boxing.frontend.app.install()`.
"""

from __future__ import annotations

from nicegui import ui

from paper_boxing.frontend.pages import account, login, site, sites, tokens, users


def register() -> None:
    ui.page("/login")(login.page)
    ui.page("/")(sites.page)
    ui.page("/sites/{slug}")(site.page)
    ui.page("/tokens")(tokens.page)
    ui.page("/users")(users.page)
    ui.page("/account")(account.page)
