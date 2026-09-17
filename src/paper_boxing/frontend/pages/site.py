# SPDX-FileCopyrightText: 2026 Gary Frattarola <garyf@parkviewlab.ai>
#
# SPDX-License-Identifier: MIT OR Apache-2.0

"""`/sites/{slug}`: one site. Browse its folders with breadcrumbs, upload
files into the folder being viewed, download, replace and delete a file, and
delete a folder with an explicit recursive confirmation.

Uploads are single-file at the API (docs/design.md section 4): when several
files are picked at once, the page sends them one after another against
`PUT /sites/{slug}/files/{path}` and shows a result per file. The results of
the last upload are kept in `app.storage.tab`, so they survive a reload of
that tab and are never seen from another tab or by another person.
"""

from __future__ import annotations

from typing import Any
from urllib.parse import quote, urlencode

from fastapi.responses import RedirectResponse
from nicegui import app, ui
from nicegui.events import MultiUploadEventArguments, UploadEventArguments

from paper_boxing.common.client import BackendError
from paper_boxing.common.naming import validate_site_path
from paper_boxing.common.routes import site_url
from paper_boxing.common.schema import EntryType, FileEntry
from paper_boxing.frontend import auth, backend, layout

DOWNLOAD_PREFIX = "/download"


def folder_url(slug: str, folder: str) -> str:
    return f"/sites/{slug}?{urlencode({'path': folder})}" if folder else f"/sites/{slug}"


def download_url(slug: str, path: str) -> str:
    """The frontend's own download route, which fetches the file from the backend with the caller's session."""
    return f"{DOWNLOAD_PREFIX}/{quote(slug, safe='')}/{quote(path, safe='/')}"


async def page(slug: str, path: str = "") -> RedirectResponse | None:
    token = auth.token()
    if token is None:
        return RedirectResponse(auth.login_url(folder_url(slug, path)))
    client = backend.client()
    public_sites_url = backend.config().public_sites_url

    try:
        folder = validate_site_path(path, allow_root=True)
    except ValueError as e:
        with layout.frame("Site"):
            ui.label(f"Invalid folder path: {e}").classes("text-negative")
            ui.link("Back to the site root", folder_url(slug, ""))
        return None

    try:
        site = await client.get_site(token, slug)
    except BackendError as e:
        with layout.frame("Site"):
            if e.status == 404:
                ui.label(f"There is no site '{slug}'.").classes("text-negative").mark("no-site")
                ui.link("Back to the sites", "/")
            else:
                layout.report_error(e)
        return None

    url = site_url(public_sites_url, site.slug)
    results_key = f"uploads:{site.slug}"

    with layout.frame(site.name):
        with ui.row().classes("items-center gap-2"):
            ui.label(f"/{site.slug}/").classes("font-mono text-grey-7")
            ui.link(url, url, new_tab=True).classes("font-mono").mark("site-url")
            ui.button(icon="content_copy", on_click=lambda: layout.copy_to_clipboard(url, "URL")).props(
                "flat dense round"
            ).tooltip("Copy the address")
            ui.button("Open", icon="open_in_new", on_click=lambda: ui.navigate.to(url, new_tab=True)).props(
                "flat dense no-caps"
            )
        ui.label(
            f"{site.file_count} files, {layout.human_bytes(site.bytes)}, created {layout.when(site.created_at)}"
        ).classes("text-sm text-grey-7")

        # ---- breadcrumbs ----
        with ui.row().classes("items-center gap-1").mark("breadcrumbs"):
            ui.link("root", folder_url(site.slug, "")).classes("no-underline")
            trail = ""
            for segment in folder.split("/") if folder else []:
                trail = f"{trail}/{segment}" if trail else segment
                ui.icon("chevron_right").classes("text-grey-6")
                ui.link(segment, folder_url(site.slug, trail)).classes("no-underline")

        # ---- upload ----
        with ui.card().classes("w-full gap-2"):
            ui.label(f"Upload into {folder or 'the site root'}").classes("text-lg")
            ui.label(
                "Pick one or several files. Each is sent on its own, in turn, to the single-file route; "
                "a file that exists is left as it is unless Overwrite is ticked first."
            ).classes("text-grey-7")
            overwrite = ui.checkbox("Overwrite existing files").mark("overwrite")
            uploader = (
                ui.upload(
                    multiple=True, auto_upload=True, on_multi_upload=lambda e: on_uploads(e), label="Files"
                )
                .props("flat bordered")
                .classes("w-full")
                .mark("uploader")
            )

            def stored_results() -> list[dict[str, Any]]:
                if not ui.context.client.has_socket_connection:
                    return []
                return list(app.storage.tab.get(results_key, []))

            @ui.refreshable
            def upload_results() -> None:
                results = stored_results()
                if not results:
                    return
                with ui.column().classes("gap-0 w-full").mark("upload-results"):
                    ui.label("Last upload in this tab").classes("text-sm text-grey-7")
                    for result in results:
                        with ui.row().classes("items-center gap-2").mark("upload-result"):
                            ui.icon("check_circle" if result["ok"] else "error").classes(
                                "text-positive" if result["ok"] else "text-negative"
                            )
                            ui.label(result["path"]).classes("font-mono")
                            ui.label(result["note"]).classes("text-sm")

            async def on_uploads(e: MultiUploadEventArguments) -> None:
                results: list[dict[str, Any]] = []
                for file in e.files:
                    target = f"{folder}/{file.name}" if folder else file.name
                    try:
                        content = await file.read()
                        result = await client.upload_file(
                            token, site.slug, target, content, overwrite=bool(overwrite.value)
                        )
                    except BackendError as err:
                        if err.status == 401:
                            layout.report_error(err)
                            return
                        note = layout.error_text(err)
                        if err.code == "file_exists":
                            note += " Tick Overwrite existing files and upload it again to replace it."
                        results.append({"path": target, "ok": False, "note": note})
                    else:
                        verb = "replaced" if result.replaced else "uploaded"
                        results.append(
                            {
                                "path": result.path,
                                "ok": True,
                                "note": f"{verb}, {layout.human_bytes(result.bytes)}, sha256 {result.sha256[:12]}",
                            }
                        )
                    app.storage.tab[results_key] = list(results)
                    upload_results.refresh()
                uploader.reset()
                await listing.refresh()

            upload_results()

        # ---- the listing ----
        @ui.refreshable
        async def listing() -> None:
            try:
                entries = (await client.list_files(token, site.slug, folder)).entries
            except BackendError as e:
                if e.status == 404 and folder:
                    ui.label(f"There is no folder '{folder}' in this site.").classes("text-negative")
                    ui.link("Back to the site root", folder_url(site.slug, ""))
                    return
                layout.report_error(e)
                return
            with ui.card().classes("w-full gap-1"):
                ui.label(f"Contents of {folder or 'the site root'}").classes("text-lg")
                if not entries:
                    ui.label("Nothing here yet.").classes("text-grey-7").mark("empty-folder")
                    return
                for entry in entries:
                    entry_row(entry)

        def entry_row(entry: FileEntry) -> None:
            full = f"{folder}/{entry.name}" if folder else entry.name
            is_folder = entry.type is EntryType.FOLDER
            with ui.row().classes("items-center justify-between w-full py-1").mark("entry-row"):
                with ui.row().classes("items-center gap-3"):
                    ui.icon("folder" if is_folder else "description").classes("text-grey-7")
                    if is_folder:
                        ui.link(entry.name, folder_url(site.slug, full)).classes("no-underline")
                    else:
                        ui.label(entry.name)
                    ui.label(layout.human_bytes(entry.bytes)).classes("text-sm text-grey-7")
                    ui.label(layout.when(entry.modified_at)).classes("text-sm text-grey-7")
                    if entry.sha256:
                        ui.label(entry.sha256[:12]).classes("font-mono text-xs text-grey-6").tooltip(
                            f"sha256 {entry.sha256}"
                        )
                with ui.row().classes("items-center gap-1"):
                    if is_folder:
                        ui.button(
                            "Delete folder",
                            icon="delete",
                            color="negative",
                            on_click=lambda: delete_folder(full),
                        ).props("flat dense no-caps").mark("delete-folder")
                    else:
                        ui.button(
                            "Download",
                            icon="download",
                            on_click=lambda: ui.download.from_url(download_url(site.slug, full)),
                        ).props("flat dense no-caps").mark("download-file")
                        ui.button("Replace", icon="upload_file", on_click=lambda: replace_file(full)).props(
                            "flat dense no-caps"
                        ).mark("replace-file")
                        ui.button(
                            "Delete", icon="delete", color="negative", on_click=lambda: delete_file(full)
                        ).props("flat dense no-caps").mark("delete-file")

        async def replace_file(full: str) -> None:
            with ui.dialog() as dialog, ui.card().classes("gap-3 w-96 max-w-full"):
                ui.label(f"Replace {full}").classes("text-lg")
                ui.label("The file you pick replaces it in place, whatever the picked file is called.")
                problem = ui.label().classes("text-negative")
                problem.visible = False

                async def on_upload(e: UploadEventArguments) -> None:
                    try:
                        result = await client.upload_file(
                            token, site.slug, full, await e.file.read(), overwrite=True
                        )
                    except BackendError as err:
                        if err.status == 401:
                            dialog.submit(None)
                            layout.report_error(err)
                            return
                        problem.text = layout.error_text(err)
                        problem.visible = True
                        return
                    dialog.submit(result)

                ui.upload(auto_upload=True, on_upload=on_upload, label="Pick the replacement").props(
                    "flat bordered"
                ).classes("w-full").mark("replacement")
                with ui.row().classes("justify-end w-full"):
                    ui.button("Cancel", on_click=dialog.close).props("flat no-caps")
            result = await dialog
            dialog.delete()
            if result is None:
                return
            ui.notify(f"{full} replaced ({layout.human_bytes(result.bytes)})", type="positive")
            await listing.refresh()

        async def delete_file(full: str) -> None:
            with ui.dialog() as dialog, ui.card().classes("gap-3"):
                ui.label(f"Delete the file {full}?").classes("text-lg")
                with ui.row().classes("justify-end w-full"):
                    ui.button("Cancel", on_click=dialog.close).props("flat no-caps")
                    ui.button("Delete", color="negative", on_click=lambda: dialog.submit(True)).props(
                        "no-caps"
                    ).mark("confirm-delete-file")
            confirmed = await dialog
            dialog.delete()
            if not confirmed:
                return
            try:
                await client.delete_file(token, site.slug, full)
            except BackendError as e:
                layout.report_error(e)
                return
            ui.notify(f"{full} deleted", type="positive")
            await listing.refresh()

        async def delete_folder(full: str) -> None:
            with ui.dialog() as dialog, ui.card().classes("gap-3"):
                ui.label(f"Delete the folder {full}?").classes("text-lg")
                ui.label("An empty folder goes at once. One with contents needs the recursive choice below.")
                recursive = ui.checkbox("Also delete everything inside it (recursive)").mark("recursive")
                problem = ui.label().classes("text-negative").mark("folder-problem")
                problem.visible = False
                with ui.row().classes("justify-end w-full"):
                    ui.button("Cancel", on_click=dialog.close).props("flat no-caps")
                    ui.button(
                        "Delete folder",
                        color="negative",
                        on_click=lambda: dialog.submit(bool(recursive.value)),
                    ).props("no-caps").mark("confirm-delete-folder")
            while True:
                choice = await dialog
                if choice is None:
                    break
                try:
                    await client.delete_folder(token, site.slug, full, recursive=choice)
                except BackendError as e:
                    if e.code == "folder_not_empty":
                        problem.text = layout.error_text(e)
                        problem.visible = True
                        continue
                    layout.report_error(e)
                    break
                ui.notify(f"Folder {full} deleted", type="positive")
                dialog.delete()
                await listing.refresh()
                return
            dialog.delete()

        await listing()
        await ui.context.client.connected()
        upload_results.refresh()
    return None
