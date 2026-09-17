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
from paper_boxing.frontend import auth, backend

# ParkviewLab brand (the handbook's docs/brand.md): the header carries the horizontal logo the
# website uses, built the brand's no-network way: the shapes-only mark inlined, and the wordmark
# set in Michroma embedded from the vendored font file as data, never fetched.
TEAL_DEEP = "#004f52"
TEAL = "#00C2C7"
SAGE = "#90b095"
_BRAND = resources.files("paper_boxing.frontend").joinpath("brand")


MARK_MONO = {
    "#90b095": "rgba(255,255,255,0.38)",  # the foliage, softened so the figure reads over it
    "#00C2C7": "#ffffff",  # the node-and-edge figure and its arcs
    "#004f52": "rgba(0,79,82,0.9)",  # the hexagon's fill stays the ground colour so the figure reads as a cut-out
}


def mark_svg(height_px: int = 64, *, mono: bool = True) -> str:
    """The ParkviewLab mark as inline SVG, cropped to its drawn extents and scaled to `height_px` tall.

    The vendored file draws inside a 400-unit square with wide margins (its content spans roughly
    x 60..340 and y 95..275 of that box); cropping the viewBox lets the mark sit at the wordmark's
    height without the empty margin. With `mono`, the three brand colours are re-mapped to white
    for the deep-teal header; the file itself is unchanged either way.
    """
    svg = _BRAND.joinpath("parkview_lab_mark.svg").read_text()
    width_px = round(height_px * 280 / 180)
    svg = svg.replace(
        'width="400" height="400" viewBox="0 0 400 400"',
        f'width="{width_px}" height="{height_px}" viewBox="60 95 280 180"',
        1,
    )
    if mono:
        for colour, replacement in MARK_MONO.items():
            svg = svg.replace(f'"{colour}"', f'"{replacement}"')
    return svg


def brand_css() -> str:
    """The @font-face for Michroma (embedded as base64 woff2) and the wordmark's rules."""
    font = base64.b64encode(_BRAND.joinpath("fonts", "michroma-latin.woff2").read_bytes()).decode("ascii")
    return f"""<style>
@font-face {{ font-family: 'Michroma'; font-style: normal; font-weight: 400; font-display: swap;
  src: url('data:font/woff2;base64,{font}') format('woff2'); }}
.pb-wordmark {{ font-family: 'Michroma', ui-sans-serif, system-ui, sans-serif; color: #fff; line-height: 1;
  display: flex; flex-direction: column; gap: 4px; }}
.pb-wordmark .pb-park {{ font-size: 15.5px; letter-spacing: 0.06em; }}
.pb-wordmark .pb-rule {{ height: 2px; background: {TEAL}; width: 100%; }}
.pb-wordmark .pb-lab {{ font-size: 8.5px; letter-spacing: 0.32em; }}
.pb-app {{ color: rgba(255,255,255,0.85); font-size: 15px; letter-spacing: 0.02em; }}
</style>"""


def logo() -> None:
    """The horizontal logo: the mark beside the PARKVIEW / LAB wordmark, linking to the sites page."""
    with ui.link(target="/").classes("no-underline"), ui.row().classes("items-center gap-3"):
        ui.html(mark_svg(), sanitize=False).classes("flex").mark("brand-mark")
        ui.html(
            '<div class="pb-wordmark"><span class="pb-park">PARKVIEW</span>'
            '<span class="pb-rule"></span><span class="pb-lab">LAB</span></div>',
            sanitize=False,
        ).mark("brand-wordmark")


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
    ui.colors(primary=TEAL_DEEP, secondary=SAGE, accent=TEAL)
    ui.add_head_html(brand_css())
    with ui.header().classes("items-center justify-between px-6 py-2").style(f"background:{TEAL_DEEP}"):
        with ui.row().classes("items-center gap-8"):
            logo()
            ui.label("paper-boxing").classes("pb-app")
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
