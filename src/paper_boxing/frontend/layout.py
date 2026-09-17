# SPDX-FileCopyrightText: 2026 Gary Frattarola <garyf@parkviewlab.ai>
#
# SPDX-License-Identifier: MIT OR Apache-2.0

"""What every signed-in page shares: the frame (header, navigation, the
signed-in name, sign out), the way a backend error is shown, and the small
formatting helpers.

Everything here is called from inside a page function, for one client; no
element and no per-user value is created at import time.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager, suppress
from datetime import UTC, datetime

from nicegui import ui

from paper_boxing.common.client import BackendError, BackendUnreachable
from paper_boxing.frontend import auth, backend

NAV: tuple[tuple[str, str], ...] = (
    ("Sites", "/"),
    ("Tokens", "/tokens"),
    ("Users", "/users"),
    ("Account", "/account"),
)


@contextmanager
def frame(title: str) -> Iterator[None]:
    """The header with the navigation and the signed-in person, then a centred column for the page."""
    ui.page_title(f"{title} · paper-boxing")
    with ui.header().classes("items-center justify-between px-4 py-2"):
        with ui.row().classes("items-center gap-6"):
            ui.link("paper-boxing", "/").classes("text-lg font-semibold text-white no-underline")
            for text, path in NAV:
                ui.link(text, path).classes("text-white no-underline")
        with ui.row().classes("items-center gap-2"):
            ui.icon("person").classes("text-white")
            ui.label(auth.username()).classes("text-white").mark("current-user")
            ui.button("Sign out", icon="logout", on_click=sign_out).props("flat dense no-caps color=white")
    with ui.column().classes("w-full max-w-5xl mx-auto p-6 gap-4"):
        ui.label(title).classes("text-2xl font-medium")
        yield


async def sign_out() -> None:
    """End the backend session when it still exists, forget it in this browser, and return to the sign-in page."""
    token = auth.token()
    auth.sign_out()
    if token is not None:
        with suppress(BackendError):
            await backend.client().logout(token)
    ui.navigate.to(auth.LOGIN_PATH)


def error_text(error: BackendError) -> str:
    """The message a person sees for a backend error: the backend's own words, never a traceback."""
    if isinstance(error, BackendUnreachable):
        return "The backend cannot be reached; try again in a moment."
    return error.message


def report_error(error: BackendError) -> None:
    """Show a backend error as a notification.

    A 401 means the session is no longer valid (expired, or ended by a password
    change or the account's removal), so the person is signed out here and
    sent to sign in again, returning to the page they were on.
    """
    if error.status == 401:
        auth.sign_out()
        request = ui.context.client.request
        wanted = f"{request.url.path}?{request.url.query}" if request.url.query else request.url.path
        ui.notify("Your session has ended; sign in again.", type="warning")
        ui.navigate.to(auth.login_url(wanted))
        return
    ui.notify(error_text(error), type="negative", multi_line=True, close_button="Dismiss")


def human_bytes(count: int) -> str:
    """1234567 -> '1.2 MB' (decimal units), 0 -> '0 B'."""
    value = float(count)
    for unit in ("B", "kB", "MB", "GB", "TB"):
        if value < 1000 or unit == "TB":
            return f"{int(value)} {unit}" if unit == "B" else f"{value:.1f} {unit}"
        value /= 1000
    return f"{count} B"


def when(moment: datetime | None) -> str:
    """A timestamp as 'YYYY-MM-DD HH:MM UTC', or 'never'."""
    if moment is None:
        return "never"
    return moment.astimezone(UTC).strftime("%Y-%m-%d %H:%M UTC")


def copy_to_clipboard(text: str, what: str) -> None:
    ui.clipboard.write(text)
    ui.notify(f"{what} copied", type="positive")
