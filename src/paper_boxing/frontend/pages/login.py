# SPDX-FileCopyrightText: 2026 Gary Frattarola <garyf@parkviewlab.ai>
#
# SPDX-License-Identifier: MIT OR Apache-2.0

"""`/login`: sign in, then return to the page that was asked for."""

from __future__ import annotations

from fastapi.responses import RedirectResponse
from nicegui import ui

from paper_boxing.common.client import BackendError
from paper_boxing.frontend import auth, backend, layout


async def page(next: str = "/") -> RedirectResponse | None:
    target = auth.safe_next(next)
    if auth.token() is not None:
        return RedirectResponse(target)
    ui.page_title("Sign in · paper-boxing")
    with ui.card().classes("absolute-center w-80 gap-3"):
        ui.label("paper-boxing").classes("text-2xl font-medium")
        ui.label("Sign in").classes("text-grey-7")
        username = ui.input("Username").props("outlined dense autofocus").classes("w-full").mark("username")
        password = (
            ui.input("Password", password=True, password_toggle_button=True)
            .props("outlined dense")
            .classes("w-full")
            .mark("password")
        )
        error = ui.label().classes("text-negative").mark("login-error")
        error.visible = False

        async def submit() -> None:
            error.visible = False
            name = (username.value or "").strip()
            secret = password.value or ""
            if not name or not secret:
                error.text = "Enter your username and password."
                error.visible = True
                return
            try:
                result = await backend.client().login(name, secret)
            except BackendError as e:
                error.text = layout.error_text(e)
                error.visible = True
                return
            auth.sign_in(result.token, result.user.username, result.user.id)
            ui.navigate.to(target)

        username.on("keydown.enter", submit)
        password.on("keydown.enter", submit)
        ui.button("Sign in", on_click=submit).classes("w-full").mark("sign-in")
    return None
