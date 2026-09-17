# SPDX-FileCopyrightText: 2026 Gary Frattarola <garyf@parkviewlab.ai>
#
# SPDX-License-Identifier: MIT OR Apache-2.0

"""The integration tier: end-to-end tests against the running compose stack.

Run them with the stack up (see compose.build.yml). With
`PAPER_BOXING_INTEGRATION=1` set, as the CI job does, an unreachable stack
fails; without it, the tier is skipped, so a plain `uv run pytest` on a
machine with no stack still passes.

The stack's own settings are read from `integration.env`, the file the compose
command takes as `--env-file`, so the admin pair and the public sites URL have
one source. `admin` signs in to the real backend once for the session and
signs out at the end; `agent_tokens` mints one agent token per scope and
revokes them; the site fixtures in the test modules create their sites and
delete them. The tier leaves the stack as it found it, so the job can run
twice against one stack.

`test_backend_outage` stops the backend container. The collection hook runs
that module last, so nothing else meets a backend that is still coming back.
"""

from __future__ import annotations

import hashlib
import os
import subprocess
import time
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import quote

import httpx
import pytest

from paper_boxing.common.routes import build_path
from tests.integration._tree import TreeFile

HOST = os.environ.get("PAPER_BOXING_INTEGRATION_HOST", "127.0.0.1")
FRONTEND = f"http://{HOST}:35840"
SITES = f"http://{HOST}:35841"
MCP = f"http://{HOST}:35842"
BACKEND = f"http://{HOST}:35843"
REQUIRED = os.environ.get("PAPER_BOXING_INTEGRATION") == "1"

BACKEND_CONTAINER = "paper-boxing-backend"
OUTAGE_MODULE = "tests.integration.test_backend_outage"

TIMEOUT = httpx.Timeout(30.0, connect=5.0)

# The JSON-RPC handshake a raw client sends, and the headers Streamable HTTP needs.
MCP_INIT: dict[str, Any] = {
    "jsonrpc": "2.0",
    "id": 1,
    "method": "initialize",
    "params": {
        "protocolVersion": "2025-06-18",
        "capabilities": {},
        "clientInfo": {"name": "integration", "version": "0"},
    },
}
MCP_HEADERS = {"Accept": "application/json, text/event-stream", "Content-Type": "application/json"}


def read_env_file(path: Path) -> dict[str, str]:
    """The `KEY=value` lines of a compose env file; comments and blank lines skipped."""
    values: dict[str, str] = {}
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        key, _, value = line.partition("=")
        values[key.strip()] = value.strip()
    return values


STACK_ENV = read_env_file(Path(__file__).with_name("integration.env"))
ADMIN_USERNAME = STACK_ENV["PAPER_BOXING_ADMIN_USERNAME"]
ADMIN_PASSWORD = STACK_ENV["PAPER_BOXING_ADMIN_PASSWORD"]
PUBLIC_SITES_URL = STACK_ENV["PAPER_BOXING_PUBLIC_SITES_URL"].rstrip("/")


# ---------------------------------------------------------------------------
# The stack


def _stack_answers() -> bool:
    try:
        return httpx.get(f"{BACKEND}/health", timeout=2.0).status_code == 200
    except httpx.HTTPError:
        return False


@pytest.fixture(scope="session", autouse=True)
def stack() -> None:
    if not _stack_answers():
        if REQUIRED:
            pytest.fail(f"PAPER_BOXING_INTEGRATION=1 but the stack does not answer at {BACKEND}/health")
        pytest.skip(f"no paper-boxing stack at {BACKEND}; start it with tests/integration/compose.build.yml")


def pytest_collection_modifyitems(items: list[pytest.Item]) -> None:
    """Run the backend-outage module last; the sort is stable, so nothing else moves."""
    items.sort(key=lambda item: getattr(getattr(item, "module", None), "__name__", "") == OUTAGE_MODULE)


def wait_for_health(base: str, timeout: float) -> None:
    """Poll `/health` until it answers 200, or fail after `timeout` seconds."""
    deadline = time.monotonic() + timeout
    while True:
        if _answers(base):
            return
        if time.monotonic() > deadline:
            pytest.fail(f"{base}/health did not answer within {timeout:.0f} seconds")
        time.sleep(0.5)


def _answers(base: str) -> bool:
    try:
        return httpx.get(f"{base}/health", timeout=2.0).status_code == 200
    except httpx.HTTPError:
        return False


def docker_exec(*command: str) -> str:
    """Run a command in the backend container: the one place the data volume can be inspected."""
    result = subprocess.run(
        ["docker", "exec", BACKEND_CONTAINER, *command], capture_output=True, text=True, check=True
    )
    return result.stdout


def docker(*command: str) -> None:
    subprocess.run(["docker", *command], capture_output=True, text=True, check=True)


# ---------------------------------------------------------------------------
# The backend's REST API under one bearer


class Rest:
    """The REST API of docs/api.md under one bearer, over one connection; each call asserts its status."""

    def __init__(self, token: str) -> None:
        self.token = token
        self.http = httpx.Client(
            base_url=BACKEND, headers={"Authorization": f"Bearer {token}"}, timeout=TIMEOUT
        )

    def close(self) -> None:
        self.http.close()

    @staticmethod
    def _expect(response: httpx.Response, *statuses: int) -> httpx.Response:
        assert response.status_code in statuses, (response.request.url, response.status_code, response.text)
        return response

    # ---- sites and files ----

    def create_site(self, name: str) -> dict[str, Any]:
        return self._expect(self.http.post(build_path("create_site"), json={"name": name}), 201).json()

    def get_site(self, slug: str) -> httpx.Response:
        return self.http.get(build_path("get_site", slug=slug))

    def delete_site(self, slug: str) -> None:
        self._expect(self.http.delete(build_path("delete_site", slug=slug), params={"confirm": slug}), 204)

    def delete_site_if_present(self, slug: str) -> None:
        """Teardown: remove the site unless a test has already removed it."""
        self._expect(
            self.http.delete(build_path("delete_site", slug=slug), params={"confirm": slug}), 204, 404
        )

    def upload(self, slug: str, path: str, data: bytes, *, overwrite: bool = False) -> dict[str, Any]:
        """PUT one file as the raw body: 201 for a new file, 200 for a replacement."""
        response = self.http.put(
            build_path("upload_file", slug=slug, path=path),
            content=data,
            params={"overwrite": "true" if overwrite else "false"},
        )
        return self._expect(response, 200 if overwrite else 201).json()

    def list_files(self, slug: str, path: str = "") -> dict[str, Any]:
        response = self.http.get(build_path("list_files", slug=slug), params={"path": path})
        return self._expect(response, 200).json()

    def delete_file(self, slug: str, path: str) -> None:
        self._expect(self.http.delete(build_path("delete_file", slug=slug, path=path)), 204)

    def delete_folder(self, slug: str, path: str, *, recursive: bool = False) -> None:
        response = self.http.delete(
            build_path("delete_folder", slug=slug, path=path),
            params={"recursive": "true" if recursive else "false"},
        )
        self._expect(response, 204)

    # ---- tokens and the session ----

    def create_token(self, name: str, scope: str) -> dict[str, Any]:
        response = self.http.post(build_path("create_token"), json={"name": name, "scope": scope})
        return self._expect(response, 201).json()

    def revoke_token(self, token_id: str) -> None:
        self._expect(self.http.delete(build_path("revoke_token", token_id=token_id)), 204)

    def logout(self) -> None:
        self._expect(self.http.post(build_path("logout")), 204)


@pytest.fixture(scope="session")
def admin(stack: None) -> Iterator[Rest]:
    """The stack's first account, signed in through the real backend for the session and signed out at the end."""
    login = httpx.post(
        f"{BACKEND}{build_path('login')}",
        json={"username": ADMIN_USERNAME, "password": ADMIN_PASSWORD},
        timeout=TIMEOUT,
    )
    assert login.status_code == 200, login.text
    rest = Rest(login.json()["token"])
    try:
        yield rest
    finally:
        rest.logout()
        rest.close()


@dataclass(frozen=True)
class AgentToken:
    id: str
    secret: str
    scope: str


SCOPES = ("read_only", "read_write", "remove_destructive")


@pytest.fixture(scope="session")
def agent_tokens(admin: Rest) -> Iterator[dict[str, AgentToken]]:
    """One agent token per scope, minted through the real backend and revoked at the end of the session."""
    minted: dict[str, AgentToken] = {}
    for scope in SCOPES:
        created = admin.create_token(f"integration-{scope}", scope)
        minted[scope] = AgentToken(id=created["token"]["id"], secret=created["secret"], scope=scope)
    try:
        yield minted
    finally:
        for token in minted.values():
            admin.revoke_token(token.id)


# ---------------------------------------------------------------------------
# A deployed site and what nginx must serve of it


@dataclass(frozen=True)
class Deployed:
    """A site the tier created, with the tree it uploaded."""

    slug: str
    tree: tuple[TreeFile, ...]

    def file(self, path: str) -> TreeFile:
        return next(file for file in self.tree if file.path == path)

    @property
    def paths(self) -> list[str]:
        return [file.path for file in self.tree]


def fetch(slug: str, path: str = "", **headers: str) -> httpx.Response:
    """GET a path of a site through nginx, without following redirects; `path` is percent-encoded here."""
    return httpx.get(
        f"{SITES}/{slug}/{quote(path, safe='/')}", headers=headers, timeout=TIMEOUT, follow_redirects=False
    )


def media_type(response: httpx.Response) -> str:
    """The Content-Type without its parameters."""
    return response.headers.get("content-type", "").split(";")[0].strip()


def assert_served_as_uploaded(slug: str, file: TreeFile) -> None:
    """nginx answers the file byte for byte, with the type its extension maps to and the headers the design fixes."""
    response = fetch(slug, file.path)
    assert response.status_code == 200, (file.path, response.status_code)
    assert response.content == file.data, f"{file.path}: the served bytes differ from the uploaded ones"
    assert hashlib.sha256(response.content).hexdigest() == file.sha256, file.path
    assert int(response.headers["content-length"]) == len(file.data), file.path
    assert media_type(response) == file.media_type, (file.path, response.headers.get("content-type"))
    assert response.headers.get("cache-control") == "no-cache", file.path
    assert "etag" in response.headers, file.path


def assert_nothing_remains_on_the_volume(slug: str) -> None:
    """After a site is deleted, neither its folder under sites/ nor a parked copy under staging/ is left."""
    sites = docker_exec("ls", "-A", "/data/sites").split()
    staging = docker_exec("ls", "-A", "/data/staging").split()
    assert slug not in sites, sites
    assert not any(entry.startswith(slug) for entry in staging), staging
