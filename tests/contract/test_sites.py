# SPDX-FileCopyrightText: 2026 Gary Frattarola <garyf@parkviewlab.ai>
#
# SPDX-License-Identifier: MIT OR Apache-2.0

"""Sites: create, list, read, delete, and the slug and confirmation rules."""

from __future__ import annotations

from fastapi.testclient import TestClient

from paper_boxing.common.schema import ErrorBody, ErrorCode, Site, SiteList
from tests.contract.conftest import Actors, bearer


def _code(resp) -> ErrorCode:
    return ErrorBody.model_validate(resp.json()).error.code


def test_create_and_list(api: TestClient, actors: Actors) -> None:
    s = bearer(actors.session)
    assert SiteList.model_validate(api.get("/api/v1/sites", headers=s).json()).sites == []
    resp = api.post("/api/v1/sites", json={"name": "  PensaForma: Design  "}, headers=s)
    assert resp.status_code == 201, resp.text
    site = Site.model_validate(resp.json())
    assert site.slug == "pensaforma-design"
    assert site.name == "PensaForma: Design"
    assert site.url.endswith("/pensaforma-design/")
    assert site.file_count == 0 and site.bytes == 0
    api.post("/api/v1/sites", json={"name": "Alpha"}, headers=s)
    listed = SiteList.model_validate(api.get("/api/v1/sites", headers=s).json()).sites
    assert [x.slug for x in listed] == ["alpha", "pensaforma-design"]


def test_get_site_and_counts(api: TestClient, actors: Actors) -> None:
    s = bearer(actors.session)
    api.post("/api/v1/sites", json={"name": "Counts"}, headers=s)
    api.put("/api/v1/sites/counts/files/a.txt", content=b"abc", headers=s)
    api.put("/api/v1/sites/counts/files/d/b.txt", content=b"de", headers=s)
    site = Site.model_validate(api.get("/api/v1/sites/counts", headers=s).json())
    assert site.file_count == 2
    assert site.bytes == 5
    assert api.get("/api/v1/sites/nowhere", headers=s).status_code == 404


def test_slug_must_be_unique(api: TestClient, actors: Actors) -> None:
    s = bearer(actors.session)
    assert api.post("/api/v1/sites", json={"name": "Same Name"}, headers=s).status_code == 201
    resp = api.post("/api/v1/sites", json={"name": "same-name"}, headers=s)
    assert resp.status_code == 409
    assert _code(resp) is ErrorCode.SITE_EXISTS


def test_name_without_a_usable_slug_is_400(api: TestClient, actors: Actors) -> None:
    resp = api.post("/api/v1/sites", json={"name": "!!!"}, headers=bearer(actors.session))
    assert resp.status_code == 400
    assert _code(resp) is ErrorCode.INVALID_NAME


def test_name_is_validated(api: TestClient, actors: Actors) -> None:
    resp = api.post("/api/v1/sites", json={"name": ""}, headers=bearer(actors.session))
    assert resp.status_code == 422
    assert _code(resp) is ErrorCode.VALIDATION_ERROR


def test_delete_needs_matching_confirm(api: TestClient, actors: Actors) -> None:
    s = bearer(actors.session)
    api.post("/api/v1/sites", json={"name": "Doomed"}, headers=s)
    api.put("/api/v1/sites/doomed/files/x.txt", content=b"x", headers=s)
    for params in ({}, {"confirm": "doome"}, {"confirm": "DOOMED"}):
        resp = api.delete("/api/v1/sites/doomed", params=params, headers=s)
        assert resp.status_code == 400, params
        assert _code(resp) is ErrorCode.CONFIRM_MISMATCH
    assert api.get("/api/v1/sites/doomed", headers=s).status_code == 200
    assert api.delete("/api/v1/sites/doomed", params={"confirm": "doomed"}, headers=s).status_code == 204
    assert api.get("/api/v1/sites/doomed", headers=s).status_code == 404
    assert api.delete("/api/v1/sites/doomed", params={"confirm": "doomed"}, headers=s).status_code == 404
