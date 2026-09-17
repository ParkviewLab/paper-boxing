# SPDX-FileCopyrightText: 2026 Gary Frattarola <garyf@parkviewlab.ai>
#
# SPDX-License-Identifier: MIT OR Apache-2.0

"""`/`: the sites. Create one, copy or open its public address, open its page,
and delete it after typing its slug back."""

from __future__ import annotations

from fastapi.responses import RedirectResponse
from nicegui import ui

from paper_boxing.common.client import BackendError
from paper_boxing.common.naming import slug_from_name
from paper_boxing.common.routes import site_url
from paper_boxing.common.schema import Site
from paper_boxing.frontend import auth, backend, layout


async def page() -> RedirectResponse | None:
    token = auth.token()
    if token is None:
        return RedirectResponse(auth.login_url("/"))
    client = backend.client()
    public_sites_url = backend.config().public_sites_url

    with layout.frame("Sites"):
        with ui.card().classes("w-full"):
            ui.label("Create a site").classes("text-lg")
            ui.label(
                "The slug is derived from the display name, becomes part of the site's address, and never changes."
            ).classes("text-grey-7")
            with ui.row().classes("items-center gap-3 w-full"):
                name = ui.input("Display name").props("outlined dense").classes("grow").mark("site-name")
                preview = ui.label().classes("font-mono text-grey-7").mark("slug-preview")
                ui.button("Create", icon="add", on_click=lambda: create()).mark("create-site")

            def show_preview() -> None:
                text = (name.value or "").strip()
                if not text:
                    preview.text = ""
                    return
                try:
                    preview.text = f"/{slug_from_name(text)}/"
                except ValueError:
                    preview.text = "no slug can be derived from that name"

            name.on_value_change(show_preview)

            async def create() -> None:
                text = (name.value or "").strip()
                if not text:
                    ui.notify("Enter a display name for the site.", type="warning")
                    return
                try:
                    site = await client.create_site(token, text)
                except BackendError as e:
                    layout.report_error(e)
                    return
                name.value = ""
                ui.notify(f"Site '{site.name}' created at /{site.slug}/", type="positive")
                await site_list.refresh()

        @ui.refreshable
        async def site_list() -> None:
            try:
                sites = await client.list_sites(token)
            except BackendError as e:
                layout.report_error(e)
                return
            if not sites:
                ui.label("No sites yet.").classes("text-grey-7").mark("no-sites")
                return
            for site in sites:
                site_row(site)

        def site_row(site: Site) -> None:
            url = site_url(public_sites_url, site.slug)
            with (
                ui.card().classes("w-full").mark("site-row"),
                ui.row().classes("items-center justify-between w-full"),
            ):
                with ui.column().classes("gap-1"):
                    ui.link(site.name, f"/sites/{site.slug}").classes("text-lg no-underline")
                    with ui.row().classes("items-center gap-2"):
                        ui.link(url, url, new_tab=True).classes("font-mono text-sm")
                        ui.button(
                            icon="content_copy", on_click=lambda: layout.copy_to_clipboard(url, "URL")
                        ).props("flat dense round").tooltip("Copy the address")
                    ui.label(
                        f"{site.file_count} files, {layout.human_bytes(site.bytes)}, "
                        f"created {layout.when(site.created_at)}"
                    ).classes("text-sm text-grey-7")
                with ui.row().classes("items-center gap-2"):
                    ui.button(
                        "Open", icon="open_in_new", on_click=lambda: ui.navigate.to(url, new_tab=True)
                    ).props("flat no-caps")
                    ui.button(
                        "Manage", icon="folder", on_click=lambda: ui.navigate.to(f"/sites/{site.slug}")
                    ).props("flat no-caps")
                    ui.button(
                        "Delete", icon="delete", color="negative", on_click=lambda: confirm_delete(site)
                    ).props("flat no-caps").mark("delete-site")

        async def confirm_delete(site: Site) -> None:
            with ui.dialog() as dialog, ui.card().classes("gap-3"):
                ui.label(f"Delete the site '{site.name}' and every file in it?").classes("text-lg")
                ui.label("This cannot be undone. Type the site's slug to confirm:")
                ui.label(site.slug).classes("font-mono")
                typed = (
                    ui.input("Slug").props("outlined dense autofocus").classes("w-full").mark("confirm-slug")
                )
                with ui.row().classes("justify-end w-full"):
                    ui.button("Cancel", on_click=dialog.close).props("flat no-caps")
                    ui.button(
                        "Delete site", color="negative", on_click=lambda: dialog.submit(typed.value or "")
                    ).props("no-caps").mark("confirm-delete-site")
            confirm = await dialog
            dialog.delete()
            if confirm is None:
                return
            try:
                await client.delete_site(token, site.slug, confirm=confirm.strip())
            except BackendError as e:
                layout.report_error(e)
                return
            ui.notify(f"Site '{site.name}' deleted", type="positive")
            await site_list.refresh()

        await site_list()
    return None
