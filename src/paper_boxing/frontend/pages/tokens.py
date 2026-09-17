# SPDX-FileCopyrightText: 2026 Gary Frattarola <garyf@parkviewlab.ai>
#
# SPDX-License-Identifier: MIT OR Apache-2.0

"""`/tokens`: agent tokens. Create one with a scope (the secret is shown once,
with a copy button), list every user's tokens, revoke one."""

from __future__ import annotations

from fastapi.responses import RedirectResponse
from nicegui import ui

from paper_boxing.common.client import BackendError
from paper_boxing.common.schema import Token
from paper_boxing.common.scopes import Scope
from paper_boxing.frontend import auth, backend, layout

SCOPE_LABELS: dict[Scope, str] = {
    Scope.READ_ONLY: "Read only: list sites and download files",
    Scope.READ_WRITE: "Read and write: also create sites and upload files",
    Scope.REMOVE_DESTRUCTIVE: "Remove and destructive: also delete files, folders and sites",
}


async def page() -> RedirectResponse | None:
    token = auth.token()
    if token is None:
        return RedirectResponse(auth.login_url("/tokens"))
    client = backend.client()

    with layout.frame("Agent tokens"):
        with ui.card().classes("w-full gap-2"):
            ui.label("Create a token").classes("text-lg")
            ui.label(
                "An agent presents the token to the MCP server or the REST API; the backend enforces its scope "
                "on every call. A token does not expire; it lives until revoked."
            ).classes("text-grey-7")
            name = ui.input("Name", placeholder="for example: claude on the laptop").props("outlined dense")
            name.classes("w-full").mark("token-name")
            scope = ui.radio(
                {s.value: label for s, label in SCOPE_LABELS.items()}, value=Scope.READ_ONLY.value
            )
            scope.mark("token-scope")
            ui.button("Create token", icon="key", on_click=lambda: create()).mark("create-token")

            async def create() -> None:
                text = (name.value or "").strip()
                if not text:
                    ui.notify("Give the token a name.", type="warning")
                    return
                try:
                    created = await client.create_token(token, text, Scope(scope.value))
                except BackendError as e:
                    layout.report_error(e)
                    return
                name.value = ""
                await token_list.refresh()
                show_secret_once(created.token, created.secret)

        def show_secret_once(created: Token, secret: str) -> None:
            with ui.dialog().props("persistent") as dialog, ui.card().classes("gap-3 w-[36rem] max-w-full"):
                ui.label(f"Token '{created.name}' created").classes("text-lg")
                ui.label(
                    "This secret is shown once and is not stored anywhere readable. Copy it now; "
                    "if it is lost, revoke the token and create another."
                )
                with ui.row().classes("items-center gap-2 w-full"):
                    ui.input(value=secret).props("readonly outlined dense").classes("grow font-mono").mark(
                        "token-secret"
                    )
                    ui.button(
                        "Copy",
                        icon="content_copy",
                        on_click=lambda: layout.copy_to_clipboard(secret, "Token"),
                    ).props("no-caps").mark("copy-secret")
                ui.label(f"Scope: {SCOPE_LABELS[created.scope]}").classes("text-sm text-grey-7")
                with ui.row().classes("justify-end w-full"):
                    ui.button("Done", on_click=dialog.delete).props("no-caps").mark("secret-done")
            dialog.open()

        @ui.refreshable
        async def token_list() -> None:
            try:
                tokens = await client.list_tokens(token)
            except BackendError as e:
                layout.report_error(e)
                return
            ui.label("Live tokens, every user's").classes("text-lg")
            if not tokens:
                ui.label("No agent tokens.").classes("text-grey-7").mark("no-tokens")
                return
            for item in tokens:
                token_row(item)

        def token_row(item: Token) -> None:
            with (
                ui.card().classes("w-full").mark("token-row"),
                ui.row().classes("items-center justify-between w-full"),
            ):
                with ui.column().classes("gap-1"):
                    with ui.row().classes("items-center gap-2"):
                        ui.label(item.name).classes("text-lg")
                        ui.label(item.scope.value).classes("font-mono text-sm bg-grey-3 px-2 rounded")
                    ui.label(
                        f"owner {item.username}, created {layout.when(item.created_at)}, "
                        f"last used {layout.when(item.last_used_at)}"
                    ).classes("text-sm text-grey-7")
                ui.button(
                    "Revoke", icon="block", color="negative", on_click=lambda: confirm_revoke(item)
                ).props("flat no-caps").mark("revoke-token")

        async def confirm_revoke(item: Token) -> None:
            with ui.dialog() as dialog, ui.card().classes("gap-3"):
                ui.label(f"Revoke the token '{item.name}' of {item.username}?").classes("text-lg")
                ui.label("Every agent using it is refused from now on.")
                with ui.row().classes("justify-end w-full"):
                    ui.button("Cancel", on_click=dialog.close).props("flat no-caps")
                    ui.button("Revoke", color="negative", on_click=lambda: dialog.submit(True)).props(
                        "no-caps"
                    ).mark("confirm-revoke")
            confirmed = await dialog
            dialog.delete()
            if not confirmed:
                return
            try:
                await client.revoke_token(token, item.id)
            except BackendError as e:
                layout.report_error(e)
                return
            ui.notify(f"Token '{item.name}' revoked", type="positive")
            await token_list.refresh()

        await token_list()
    return None
