# SPDX-FileCopyrightText: 2026 Gary Frattarola <garyf@parkviewlab.ai>
#
# SPDX-License-Identifier: MIT OR Apache-2.0

"""The backend's ops endpoints and the data layout it creates at startup."""

from __future__ import annotations

import os
from pathlib import Path

from fastapi.testclient import TestClient

from paper_boxing.common.config import VERSION
from paper_boxing.common.schema import ErrorBody, ErrorCode, Health


def test_health(backend_client: TestClient) -> None:
    resp = backend_client.get("/health")
    assert resp.status_code == 200
    health = Health.model_validate(resp.json())
    assert health.ok is True
    assert health.version == VERSION
    assert health.uptime_seconds >= 0


def test_admin_version(backend_client: TestClient) -> None:
    body = backend_client.get("/admin/version").json()
    assert body["name"] == "paper-boxing-backend"
    assert body["version"] == VERSION
    assert body["data_dir"] == os.environ["PAPER_BOXING_DATA_DIR"]
    assert body["public_sites_url"].startswith("http://")


def test_docs(backend_client: TestClient) -> None:
    assert backend_client.get("/docs").status_code == 200
    assert backend_client.get("/openapi.json").status_code == 200


def test_lifespan_creates_the_data_layout(backend_client: TestClient) -> None:
    data_dir = Path(os.environ["PAPER_BOXING_DATA_DIR"])
    assert (data_dir / "sites").is_dir()
    assert (data_dir / "staging").is_dir()


def test_unknown_route_uses_the_error_body(backend_client: TestClient) -> None:
    resp = backend_client.get("/api/v1/nowhere")
    assert resp.status_code == 404
    body = ErrorBody.model_validate(resp.json())
    assert body.error.code is ErrorCode.NOT_FOUND
