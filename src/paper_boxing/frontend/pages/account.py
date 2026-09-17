# SPDX-FileCopyrightText: 2026 Gary Frattarola <garyf@parkviewlab.ai>
#
# SPDX-License-Identifier: MIT OR Apache-2.0

"""`/account`: change your own password."""

from __future__ import annotations

from nicegui import ui

from paper_boxing.common.client import BackendError
from paper_boxing.common.schema import ChangePasswordRequest
from paper_boxing.frontend import auth, backend, layout

MIN_PASSWORD_LENGTH = ChangePasswordRequest.model_fields["new"].metadata[0].min_length


async def page() -> None:
    token = auth.session_token()
    client = backend.client()

    with layout.frame("Account"):
        ui.label(f"Signed in as {auth.username()}").classes("text-grey-7")
        with ui.card().classes("w-full max-w-md gap-2"):
            ui.label("Change your password").classes("text-lg")
            ui.label(
                f"At least {MIN_PASSWORD_LENGTH} characters. Your other sessions are signed out; "
                "this one and your agent tokens stay."
            ).classes("text-grey-7")
            current = (
                ui.input("Current password", password=True, password_toggle_button=True)
                .props("outlined dense")
                .classes("w-full")
                .mark("current-password")
            )
            new = (
                ui.input("New password", password=True, password_toggle_button=True)
                .props("outlined dense")
                .classes("w-full")
                .mark("new-password")
            )
            again = (
                ui.input("New password, again", password=True, password_toggle_button=True)
                .props("outlined dense")
                .classes("w-full")
                .mark("new-password-again")
            )

            async def change() -> None:
                old, fresh, repeat = current.value or "", new.value or "", again.value or ""
                if not old:
                    ui.notify("Enter your current password.", type="warning")
                    return
                if len(fresh) < MIN_PASSWORD_LENGTH:
                    ui.notify(
                        f"The new password needs at least {MIN_PASSWORD_LENGTH} characters.", type="warning"
                    )
                    return
                if fresh != repeat:
                    ui.notify("The new password and its repeat differ.", type="warning")
                    return
                try:
                    await client.change_password(token, old, fresh)
                except BackendError as e:
                    layout.report_error(e)
                    return
                current.value = new.value = again.value = ""
                ui.notify("Password changed; your other sessions are signed out.", type="positive")

            again.on("keydown.enter", change)
            ui.button("Change password", icon="lock_reset", on_click=change).mark("change-password")
