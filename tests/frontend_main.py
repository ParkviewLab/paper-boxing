# SPDX-FileCopyrightText: 2026 Gary Frattarola <garyf@parkviewlab.ai>
#
# SPDX-License-Identifier: MIT OR Apache-2.0

"""The NiceGUI main file of the frontend tests, executed afresh for every
test by `nicegui.testing`'s `user` fixture (`main_file` in pyproject.toml).

It builds a fake backend that implements the whole contract, installs the
frontend against it through an in-process ASGI transport (the frontend's
one shared object, its `httpx.AsyncClient`, is simply given that transport),
and leaves the fake's state in `tests.frontend_support.harness`.
"""

import httpx

from paper_boxing.common.fake_backend import create_fake_backend
from paper_boxing.frontend.app import install, run
from paper_boxing.frontend.config import FrontendConfig
from tests import frontend_support as support

fake = create_fake_backend(admin=support.ADMIN, max_upload_mb=1, public_sites_url=support.PUBLIC_SITES_URL)
support.harness = support.Harness(app=fake, state=fake.state.fake)

cfg = FrontendConfig(
    host="127.0.0.1",
    port=35840,
    backend_url="http://backend",
    public_sites_url=support.PUBLIC_SITES_URL,
    storage_secret=support.STORAGE_SECRET,
)
install(
    cfg,
    http_client_factory=lambda: httpx.AsyncClient(
        transport=httpx.ASGITransport(app=fake), base_url=cfg.backend_url
    ),
)
run(cfg)
