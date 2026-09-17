# SPDX-FileCopyrightText: 2026 Gary Frattarola <garyf@parkviewlab.ai>
#
# SPDX-License-Identifier: MIT OR Apache-2.0

"""`/users`: the accounts. List them, add one, remove one; the backend refuses
to remove the last, and the page shows its reason."""

from __future__ import annotations

from nicegui import ui

from paper_boxing.common.client import BackendError
from paper_boxing.common.schema import User
from paper_boxing.frontend import auth, backend, layout


async def page() -> None:
    token = auth.session_token()
    client = backend.client()

    with layout.frame("Users"):
        with ui.card().classes("w-full gap-2"):
            ui.label("Add an account").classes("text-lg")
            ui.label(
                "Every account has the same rights. A username starts with a letter or digit and may contain "
                "letters, digits, dots, underscores and hyphens; a password has at least 8 characters."
            ).classes("text-grey-7")
            with ui.row().classes("items-center gap-3 w-full"):
                username = ui.input("Username").props("outlined dense").classes("grow").mark("new-username")
                password = (
                    ui.input("Password", password=True, password_toggle_button=True)
                    .props("outlined dense")
                    .classes("grow")
                    .mark("new-password")
                )
                ui.button("Add", icon="person_add", on_click=lambda: add()).mark("add-user")

            async def add() -> None:
                name = (username.value or "").strip()
                secret = password.value or ""
                if not name or not secret:
                    ui.notify("Enter a username and a password.", type="warning")
                    return
                try:
                    user = await client.create_user(token, name, secret)
                except BackendError as e:
                    layout.report_error(e)
                    return
                username.value = ""
                password.value = ""
                ui.notify(f"Account '{user.username}' added", type="positive")
                await user_list.refresh()

        @ui.refreshable
        async def user_list() -> None:
            try:
                users = await client.list_users(token)
            except BackendError as e:
                layout.report_error(e)
                return
            ui.label("Accounts").classes("text-lg")
            for user in users:
                user_row(user)

        def user_row(user: User) -> None:
            mine = user.id == auth.user_id()
            with (
                ui.card().classes("w-full").mark("user-row", f"user-{user.id}"),
                ui.row().classes("items-center justify-between w-full"),
            ):
                with ui.column().classes("gap-1"):
                    with ui.row().classes("items-center gap-2"):
                        ui.label(user.username).classes("text-lg")
                        if mine:
                            ui.label("you").classes("text-sm bg-grey-3 px-2 rounded")
                    ui.label(f"created {layout.when(user.created_at)}").classes("text-sm text-grey-7")
                ui.button(
                    "Remove", icon="person_remove", color="negative", on_click=lambda: confirm_remove(user)
                ).props("flat no-caps").mark("remove-user")

        async def confirm_remove(user: User) -> None:
            mine = user.id == auth.user_id()
            detail = "Every session and agent token of that account is revoked."
            if mine:
                detail += " This is your own account: you are signed out as soon as it is removed."
            if not await layout.confirm(
                f"Remove the account '{user.username}'?", detail, "Remove", mark="confirm-remove-user"
            ):
                return
            try:
                await client.delete_user(token, user.id)
            except BackendError as e:
                layout.report_error(e)
                return
            if mine:
                auth.sign_out()
                ui.navigate.to(auth.LOGIN_PATH)
                return
            ui.notify(f"Account '{user.username}' removed", type="positive")
            await user_list.refresh()

        await user_list()
