# SPDX-FileCopyrightText: 2026 Gary Frattarola <garyf@parkviewlab.ai>
#
# SPDX-License-Identifier: MIT OR Apache-2.0

"""One site's page: browsing with breadcrumbs, the upload flow (several files
in turn, the 409 on an existing file, the overwrite path), download, replace,
delete a file, delete a folder with the recursive confirmation, and the
per-tab memory of the last upload."""

from __future__ import annotations

import hashlib

from nicegui import ui
from nicegui.elements.upload_files import SmallFileUpload
from nicegui.testing import User

from paper_boxing.common.fake_backend import FakeState
from tests import frontend_support as support


def _upload(name: str, content: bytes) -> SmallFileUpload:
    return SmallFileUpload(name, "application/octet-stream", content)


async def _uploader(user: User, marker: str = "uploader") -> ui.upload:
    await user.should_see(marker=marker)
    element = user.find(marker=marker).elements.pop()
    assert isinstance(element, ui.upload)
    return element


async def test_upload_then_409_then_overwrite(user: User, frontend_state: FakeState) -> None:
    slug = await support.seed_site("Pages")
    await support.sign_in(user, *support.ADMIN, at=f"/sites/{slug}")
    await user.should_see(marker="empty-folder")

    uploader = await _uploader(user)
    await uploader.handle_uploads([_upload("index.html", b"<p>one</p>")])
    await user.should_see("uploaded, 10 B")
    await user.should_see("index.html")
    assert await support.file_content(slug, "index.html") == b"<p>one</p>"

    await uploader.handle_uploads([_upload("index.html", b"<p>two</p>")])
    await user.should_see("'index.html' exists; pass overwrite=true to replace it.")
    await user.should_see("Tick Overwrite existing files")
    assert await support.file_content(slug, "index.html") == b"<p>one</p>"

    user.find(marker="overwrite").click()
    await uploader.handle_uploads([_upload("index.html", b"<p>two</p>")])
    await user.should_see("replaced, 10 B")
    assert await support.file_content(slug, "index.html") == b"<p>two</p>"

    uploads = [r for r in frontend_state.requests if r.route == "upload_file"]
    assert len(uploads) == 3 and all(r.via is None for r in uploads)


async def test_several_files_go_one_after_another_with_a_result_each(
    user: User, frontend_state: FakeState
) -> None:
    slug = await support.seed_site("Batch", {"b.css": b"old"})
    await support.sign_in(user, *support.ADMIN, at=f"/sites/{slug}")
    uploader = await _uploader(user)
    before = len(frontend_state.requests)
    await uploader.handle_uploads(
        [_upload("a.html", b"<p>"), _upload("b.css", b"p{}"), _upload("c.js", b";")]
    )
    await user.should_see("c.js")
    results = user.find(marker="upload-result").elements
    assert len(results) == 3
    texts = [
        " ".join(str(child.text) for child in row.descendants() if isinstance(child, ui.label))
        for row in results
    ]
    ordered = sorted(texts, key=lambda t: t.split()[0])
    assert ordered[0].startswith("a.html uploaded, 3 B")
    assert "'b.css' exists" in ordered[1]
    assert ordered[2].startswith("c.js uploaded, 1 B")
    # one PUT per file, in the order picked, and no batch route
    uploaded = [r.route for r in frontend_state.requests[before:] if r.route != "list_files"]
    assert uploaded == ["upload_file", "upload_file", "upload_file"]
    assert await support.file_content(slug, "b.css") == b"old"


async def test_browse_folders_with_breadcrumbs(user: User) -> None:
    slug = await support.seed_site(
        "Tree", {"docs/guide/a.html": b"a", "docs/b.txt": b"bb", "top.txt": b"top"}
    )
    await support.sign_in(user, *support.ADMIN, at=f"/sites/{slug}")
    await user.should_see("docs")
    await user.should_see("top.txt")
    user.find("docs").click()
    await user.should_see("b.txt")
    await user.should_see("guide")
    await user.should_not_see("top.txt")
    user.find("guide").click()
    await user.should_see("a.html")
    with user.scope(marker="breadcrumbs"):
        await user.should_see("root")
        await user.should_see("docs")
        await user.should_see("guide")
        user.find("root").click()
    await user.should_see("top.txt")


async def test_download_hands_over_the_bytes(user: User) -> None:
    content = b"\x89PNG\r\n\x1a\n" + bytes(range(256))
    slug = await support.seed_site("Pics", {"img/pic.png": content})
    await support.sign_in(user, *support.ADMIN, at=f"/sites/{slug}?path=img")
    await user.should_see("pic.png")
    user.find(marker="download-file").click()
    response = await user.download.next()
    assert response.status_code == 200
    assert response.content == content
    assert response.headers["content-type"] == "image/png"
    assert response.headers["content-disposition"].startswith('attachment; filename="pic.png"')
    assert hashlib.sha256(response.content).hexdigest() == hashlib.sha256(content).hexdigest()


async def test_download_without_a_session_is_refused(user: User) -> None:
    slug = await support.seed_site("Locked", {"a.txt": b"a"})
    response = await user.http_client.get(f"/download/{slug}/a.txt")
    assert response.status_code == 303  # the middleware sends the browser to sign in
    assert response.headers["location"].startswith("/login?next=")


async def test_replace_a_file_in_place(user: User) -> None:
    slug = await support.seed_site("Swap", {"style.css": b"old"})
    await support.sign_in(user, *support.ADMIN, at=f"/sites/{slug}")
    await user.should_see("style.css")
    user.find(marker="replace-file").click()
    replacement = await _uploader(user, "replacement")
    await replacement.handle_uploads([_upload("whatever.css", b"new and longer")])
    await user.should_see("style.css replaced (14 B)")
    assert await support.file_content(slug, "style.css") == b"new and longer"
    assert await support.file_content(slug, "whatever.css") is None


async def test_delete_a_file_after_confirming(user: User) -> None:
    slug = await support.seed_site("Trim", {"a.txt": b"a", "b.txt": b"b"})
    await support.sign_in(user, *support.ADMIN, at=f"/sites/{slug}")
    await user.should_see("a.txt")
    with user.scope(marker="entry-0"):  # a.txt: files are listed by name
        user.find(marker="delete-file").click()
    await user.should_see(marker="confirm-delete-file")
    user.find(marker="confirm-delete-file").click()
    await user.should_see("a.txt deleted")
    with user.scope(marker="listing"):
        await user.should_not_see("a.txt")
        await user.should_see("b.txt")
    assert await support.file_content(slug, "a.txt") is None


async def test_delete_a_folder_needs_the_recursive_confirmation(
    user: User, frontend_state: FakeState
) -> None:
    slug = await support.seed_site("Prune", {"docs/a.txt": b"a", "docs/sub/b.txt": b"b"})
    await support.sign_in(user, *support.ADMIN, at=f"/sites/{slug}")
    await user.should_see("docs")
    user.find(marker="delete-folder").click()
    await user.should_see(marker="confirm-delete-folder")
    user.find(marker="confirm-delete-folder").click()
    await user.should_see("'docs' is not empty; pass recursive=true to delete everything in it")
    assert "docs/a.txt" in frontend_state.sites[slug].files
    user.find(marker="recursive").click()
    user.find(marker="confirm-delete-folder").click()
    await user.should_see("Folder docs deleted")
    await user.should_see(marker="empty-folder")
    assert frontend_state.sites[slug].files == {} and frontend_state.sites[slug].folders == {}


async def test_last_upload_is_remembered_per_tab(user: User) -> None:
    slug = await support.seed_site("Memory")
    await support.sign_in(user, *support.ADMIN, at=f"/sites/{slug}")
    uploader = await _uploader(user)
    await uploader.handle_uploads([_upload("note.txt", b"hello")])
    await user.should_see("uploaded, 5 B")

    await user.open(f"/sites/{slug}")  # the same tab, reloaded
    await user.should_see("Last upload in this tab")
    await user.should_see("uploaded, 5 B")

    other_tab = support.new_user(cookies=user.http_client.cookies)  # the same browser, another tab
    try:
        await other_tab.open(f"/sites/{slug}")
        await other_tab.should_see("note.txt")  # signed in: the session is per browser
        await other_tab.should_not_see("Last upload in this tab")  # the results are per tab
    finally:
        await other_tab.http_client.aclose()


async def test_unknown_site_and_folder(user: User) -> None:
    await support.sign_in(user, *support.ADMIN, at="/sites/nowhere")
    await user.should_see(marker="no-site")
    slug = await support.seed_site("Real")
    await user.open(f"/sites/{slug}?path=missing")
    await user.should_see("There is no folder 'missing' in this site.")
    await user.open(f"/sites/{slug}?path=../x")
    await user.should_see("Invalid folder path")
