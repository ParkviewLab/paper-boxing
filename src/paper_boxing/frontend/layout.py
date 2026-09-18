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

import base64
import json
from collections.abc import Callable, Iterator
from contextlib import contextmanager, suppress
from datetime import UTC, datetime
from importlib import resources

from nicegui import ui

from paper_boxing.common.client import BackendError, BackendUnreachable
from paper_boxing.common.config import VERSION
from paper_boxing.frontend import auth, backend

# ParkviewLab brand (the handbook's docs/brand.md): the header carries the official horizontal logo
# from the brand's own files, the white artwork on the brand's deep teal. The file @imports its
# wordmark font from Google Fonts; that line is replaced at render time with the same face embedded
# from the vendored woff2 as data, so nothing is fetched. The artwork itself is untouched.
# Beside the logo, the label names the application and the running version, "paper-boxing v<version>",
# the version being the one the package metadata gives (`paper_boxing.common.config`), so that a
# person reads which version is running from any page, as an operator reads it from /health.
TEAL_DEEP = "#004f52"
TEAL = "#00C2C7"
SAGE = "#90b095"
PAPER = "#f3eee2"
_BRAND = resources.files("paper_boxing.frontend").joinpath("brand")
_GOOGLE_IMPORT = "@import url('https://fonts.googleapis.com/css2?family=Michroma&amp;display=swap');"


def _michroma_face() -> str:
    font = base64.b64encode(_BRAND.joinpath("fonts", "michroma-latin.woff2").read_bytes()).decode("ascii")
    return (
        "@font-face{font-family:'Michroma';font-style:normal;font-weight:400;"
        f"src:url('data:font/woff2;base64,{font}') format('woff2');}}"
    )


def logo_svg(height_px: int = 60, *, variant: str = "white") -> str:
    """The official horizontal logo as inline SVG at `height_px` tall, its font embedded instead of imported.

    `variant` is "dark" (dark artwork, for a light ground) or "white" (for a dark ground), the two
    black-and-white files the brand provides.
    """
    svg = _BRAND.joinpath(f"parkview_lab_bw_horizontal_{variant}.svg").read_text()
    if _GOOGLE_IMPORT not in svg:
        raise RuntimeError("the vendored logo no longer carries the font import this code replaces")
    svg = svg.replace(_GOOGLE_IMPORT, _michroma_face(), 1)
    # The file composes the logo inside a 680 by 440 box with wide margins; the artwork itself spans
    # about x 55..570 and y 100..285. Cropping the viewBox to that keeps the drawing untouched and
    # lets it fill the header's height.
    box = (55, 100, 515, 185)
    width_px = round(height_px * box[2] / box[3])
    return svg.replace(
        'width="680" height="440" viewBox="0 0 680 440"',
        f'width="{width_px}" height="{height_px}" viewBox="{box[0]} {box[1]} {box[2]} {box[3]}"',
        1,
    )


def logo() -> None:
    """The horizontal logo, linking to the sites page."""
    with ui.link(target="/").classes("no-underline flex"):
        ui.html(logo_svg(), sanitize=False).classes("flex").mark("brand-logo")


NAV: tuple[tuple[str, str], ...] = (
    ("Sites", "/"),
    ("Tokens", "/tokens"),
    ("Users", "/users"),
    ("Account", "/account"),
)


@contextmanager
def frame(title: str) -> Iterator[None]:
    """The header with the logo, the name and version, the navigation and the signed-in person, then a
    centred column for the page."""
    ui.page_title(f"{title} · paper-boxing")
    ui.colors(primary=TEAL_DEEP, secondary=SAGE, accent=TEAL)
    with ui.header().classes("items-center justify-between px-6 py-1").style(f"background:{TEAL_DEEP}"):
        with ui.row().classes("items-center gap-8"):
            logo()
            ui.label(f"paper-boxing v{VERSION}").classes("text-white").style(
                "opacity:.85; letter-spacing:.02em"
            ).mark("brand-name")
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


@contextmanager
def confirmation(question: str, detail: str | None = None) -> Iterator[ui.dialog]:
    """A dialog asking `question`, with an optional line of detail; the caller adds its controls and buttons."""
    with ui.dialog() as dialog, ui.card().classes("gap-3"):
        ui.label(question).classes("text-lg")
        if detail:
            ui.label(detail)
        yield dialog


def confirmation_buttons(
    dialog: ui.dialog, action_label: str, on_confirm: Callable[[], None], mark: str
) -> None:
    """Cancel, which closes the dialog (an awaited dialog then resolves to None), and the destructive action."""
    with ui.row().classes("justify-end w-full"):
        ui.button("Cancel", on_click=dialog.close).props("flat no-caps")
        ui.button(action_label, color="negative", on_click=on_confirm).props("no-caps").mark(mark)


async def confirm(question: str, detail: str | None, action_label: str, *, mark: str) -> bool:
    """Ask before a destructive action; True when the person chose it, False when they cancelled."""
    with confirmation(question, detail) as dialog:
        confirmation_buttons(dialog, action_label, lambda: dialog.submit(True), mark)
    confirmed = await dialog
    dialog.delete()
    return confirmed is True


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


# `navigator.clipboard` exists in secure contexts only, and paper-boxing runs over plain HTTP on
# the LAN; the fallback is the older `document.execCommand("copy")` on a hidden, selected textarea.
# The code returns whether the text was copied; `return` and `await` make NiceGUI run it as an
# async function.
_COPY_JS = """
const text = {text};
try {{
    if (navigator.clipboard && window.isSecureContext) {{
        await navigator.clipboard.writeText(text);
        return true;
    }}
}} catch (e) {{}}
try {{
    const area = document.createElement("textarea");
    area.value = text;
    area.setAttribute("readonly", "");
    area.style.position = "fixed";
    area.style.top = "0";
    area.style.left = "0";
    area.style.opacity = "0";
    document.body.appendChild(area);
    area.focus();
    area.select();
    const copied = document.execCommand("copy");
    document.body.removeChild(area);
    return copied === true;
}} catch (e) {{
    return false;
}}
"""


async def copy_to_clipboard(text: str, what: str) -> None:
    """Copy `text` in the browser, and say so only when the browser reported success."""
    try:
        copied = await ui.run_javascript(_COPY_JS.format(text=json.dumps(text)), timeout=3.0)
    except TimeoutError:
        copied = False
    if copied is True:
        ui.notify(f"{what} copied", type="positive")
    else:
        ui.notify(
            f"Copying is not available here; select the {what.lower()} and copy it yourself.", type="warning"
        )


def select_on_focus(field: ui.input) -> ui.input:
    """Make a readonly field select its whole contents when it gains focus, so a click and a copy suffice."""
    return field.on("focus", js_handler="(e) => e.target.select()")
