# SPDX-FileCopyrightText: 2026 Gary Frattarola <garyf@parkviewlab.ai>
#
# SPDX-License-Identifier: MIT OR Apache-2.0

"""The REST client against the fake backend through an in-process ASGI transport:
every method, the error mapping, and the Via header."""

from __future__ import annotations

import hashlib
from collections.abc import AsyncIterator

import httpx
import pytest

from paper_boxing.common.client import BackendClient, BackendError, BackendUnreachable
from paper_boxing.common.fake_backend import FakeState, create_fake_backend
from paper_boxing.common.scopes import Scope

ADMIN = ("admin", "admin-password")


@pytest.fixture
async def client() -> AsyncIterator[tuple[BackendClient, FakeState]]:
    app = create_fake_backend(admin=ADMIN, max_upload_mb=1)
    http = httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://backend")
    yield BackendClient(http, via="mcp"), app.state.fake
    await http.aclose()


async def test_full_round_trip(client: tuple[BackendClient, FakeState]) -> None:
    api, fake = client
    health = await api.health()
    assert health.ok is True

    login = await api.login(*ADMIN)
    session = login.token
    assert login.user.username == ADMIN[0]

    assert await api.list_sites(session) == []
    site = await api.create_site(session, "My Site")
    assert site.slug == "my-site"
    assert (await api.get_site(session, "my-site")).slug == "my-site"

    content = b"<!doctype html><p>hello</p>"
    up = await api.upload_file(session, "my-site", "index.html", content)
    assert up.sha256 == hashlib.sha256(content).hexdigest() and up.replaced is False
    with pytest.raises(BackendError) as exc:
        await api.upload_file(session, "my-site", "index.html", b"again")
    assert exc.value.status == 409 and exc.value.code == "file_exists"
    up2 = await api.upload_file(session, "my-site", "index.html", b"again", overwrite=True)
    assert up2.replaced is True
    await api.upload_file(session, "my-site", "css/site.css", b"p{}")

    listing = await api.list_files(session, "my-site")
    assert [e.name for e in listing.entries] == ["css", "index.html"]
    down = await api.download_file(session, "my-site", "css/site.css")
    assert down.content == b"p{}"
    assert down.content_type.startswith("text/css")
    assert down.sha256 == hashlib.sha256(b"p{}").hexdigest()

    created = await api.create_token(session, "agent", Scope.READ_ONLY)
    me = await api.token_self(created.secret)
    assert me.scope is Scope.READ_ONLY and me.type == "agent"
    assert [t.id for t in await api.list_tokens(session)] == [created.token.id]
    with pytest.raises(BackendError) as exc:
        await api.create_site(created.secret, "Nope")
    assert exc.value.status == 403 and exc.value.code == "forbidden"
    await api.revoke_token(session, created.token.id)
    with pytest.raises(BackendError) as exc:
        await api.token_self(created.secret)
    assert exc.value.status == 401

    user = await api.create_user(session, "second", "second-password")
    assert [u.username for u in await api.list_users(session)] == ["admin", "second"]
    await api.delete_user(session, user.id)
    await api.change_password(session, ADMIN[1], "new-password-1")

    await api.delete_file(session, "my-site", "index.html")
    with pytest.raises(BackendError) as exc:
        await api.delete_folder(session, "my-site", "css")
    assert exc.value.code == "folder_not_empty"
    await api.delete_folder(session, "my-site", "css", recursive=True)
    assert (await api.list_files(session, "my-site")).entries == []
    await api.delete_site(session, "my-site", confirm="my-site")
    assert await api.list_sites(session) == []
    await api.logout(session)
    with pytest.raises(BackendError) as exc:
        await api.list_sites(session)
    assert exc.value.status == 401 and exc.value.code == "unauthorized"

    assert all(r.via == "mcp" for r in fake.requests)


async def test_login_failure(client: tuple[BackendClient, FakeState]) -> None:
    api, _ = client
    with pytest.raises(BackendError) as exc:
        await api.login("admin", "wrong")
    assert exc.value.status == 401
    assert exc.value.code == "invalid_credentials"
    assert "wrong password" in str(exc.value)


async def test_unreachable_backend() -> None:
    def refuse(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("connection refused", request=request)

    http = httpx.AsyncClient(transport=httpx.MockTransport(refuse), base_url="http://backend")
    api = BackendClient(http)
    with pytest.raises(BackendUnreachable) as exc:
        await api.health()
    assert exc.value.status == 503
    assert exc.value.code == "backend_unreachable"
    await api.aclose()


async def test_non_contract_error_body() -> None:
    http = httpx.AsyncClient(
        transport=httpx.MockTransport(lambda r: httpx.Response(502, text="bad gateway")),
        base_url="http://backend",
    )
    api = BackendClient(http)
    with pytest.raises(BackendError) as exc:
        await api.health()
    assert exc.value.status == 502
    assert exc.value.code == "internal_error"
    assert exc.value.message == "bad gateway"
    await api.aclose()


def test_for_base_url_builds_a_client() -> None:
    api = BackendClient.for_base_url("http://127.0.0.1:35843/")
    assert isinstance(api, BackendClient)


async def test_stream_file(client: tuple[BackendClient, FakeState]) -> None:
    api, _ = client
    session = (await api.login(*ADMIN)).token
    await api.create_site(session, "Stream")
    content = bytes(range(256)) * 20
    await api.upload_file(session, "stream", "css/site.css", content)

    async with api.stream_file(session, "stream", "css/site.css", chunk_size=1000) as file:
        assert file.path == "css/site.css"
        assert file.content_type.startswith("text/css")
        assert file.content_length == len(content)
        chunks = [chunk async for chunk in file.chunks]
    assert len(chunks) > 1
    assert b"".join(chunks) == content

    with pytest.raises(BackendError) as exc:
        async with api.stream_file(session, "stream", "css/missing.css"):
            pass
    assert exc.value.status == 404 and exc.value.code == "not_found"


async def test_stream_file_unreachable() -> None:
    def refuse(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("connection refused", request=request)

    api = BackendClient(httpx.AsyncClient(transport=httpx.MockTransport(refuse), base_url="http://backend"))
    with pytest.raises(BackendUnreachable):
        async with api.stream_file("pb_x", "site", "a.txt"):
            pass
    await api.aclose()


async def test_upload_file_from_an_async_iterator(client: tuple[BackendClient, FakeState]) -> None:
    api, fake = client
    session = (await api.login(*ADMIN)).token
    await api.create_site(session, "Chunks")
    content = b"0123456789" * 1000

    async def chunks() -> AsyncIterator[bytes]:
        for i in range(0, len(content), 4096):
            yield content[i : i + 4096]

    declared = await api.upload_file(session, "chunks", "declared.bin", chunks(), content_length=len(content))
    assert declared.bytes == len(content)
    assert declared.sha256 == hashlib.sha256(content).hexdigest()
    assert fake.sites["chunks"].files["declared.bin"].content == content

    undeclared = await api.upload_file(session, "chunks", "undeclared.bin", chunks())
    assert undeclared.bytes == len(content)
    assert fake.sites["chunks"].files["undeclared.bin"].content == content
