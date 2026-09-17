# SPDX-FileCopyrightText: 2026 Gary Frattarola <garyf@parkviewlab.ai>
#
# SPDX-License-Identifier: MIT OR Apache-2.0

"""The frontend's ops endpoints. The pages are the frontend worker's."""

from __future__ import annotations

from fastapi.testclient import TestClient

from paper_boxing.common.config import VERSION
from paper_boxing.common.schema import Health


def test_health(frontend_client: TestClient) -> None:
    resp = frontend_client.get("/health")
    assert resp.status_code == 200
    health = Health.model_validate(resp.json())
    assert health.ok is True
    assert health.version == VERSION


def test_admin_version(frontend_client: TestClient) -> None:
    body = frontend_client.get("/admin/version").json()
    assert body["name"] == "paper-boxing-frontend"
    assert body["version"] == VERSION
    assert body["backend_url"].startswith("http://")
