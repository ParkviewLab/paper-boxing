<!--
SPDX-FileCopyrightText: 2026 Gary Frattarola <garyf@parkviewlab.ai>

SPDX-License-Identifier: MIT OR Apache-2.0
-->

# In-flight ideas

Scratchpad for ideas under consideration: questions, not commitments (see the handbook's [`documentation.md`](https://github.com/ParkviewLab/handbook/blob/main/docs/documentation.md)). Do not act on an entry silently. A decision, once made, is recorded in [`decisions.md`](decisions.md), and a closed entry is removed from here.

## Open

### An all-or-nothing site replace

File operations are single-file only ([decided 2026-09-17](decisions.md#2026-09-17-single-file-operations)). If a need appears for a site to switch from one build to the next with no transient mixed state, it would be one additive route (upload a tree to staging, then swap the site's folder by rename), not a change to the existing ones. Open until the need appears.

### Narrowing the site server's mount to `sites/`

The stack mounts the whole data volume read-only into nginx and points `root` at its `sites/` tree ([decided 2026-09-17](decisions.md#2026-09-17-the-whole-volume-mounted-read-only)). Once the deployment host's Docker is 26 or newer, the mount can narrow to `sites/` alone through a Compose volume `subpath`; nothing else changes.

### Publishing `docs/` as a site

This repository's `docs/` published as a GitHub Pages site built from `main`, in the shape the handbook's [`docs-site.md`](https://github.com/ParkviewLab/handbook/blob/main/docs/docs-site.md) describes. In progress in the pull request that follows this one.

### `dev-release.yml`

The handbook's on-demand dev build, publishing `:dev` images only. Not adopted; adopt if a candidate needs exercising on the host before a release.

### Auth helpers duplicated between the MCP server and `common/`

The MCP server parses `Authorization: Bearer` (`mcp/auth.py`, `bearer_from`) and joins a pydantic validation error into one message (`mcp/tools.py`, `_validation_message`) with code of its own, duplicating what the fake backend and `common/errors.py` do. The refactor: move both into `common/` in one change, so that the three components parse a bearer and describe a validation failure identically.

### Rules duplicated between the fake backend and the real one

The bearer parsing, the `Content-Disposition` builder, the authorization ladder, the `Clock`, the path-to-`ApiError` wrapper, and the scope `CHECK` constraints that restate the enum exist twice: in `common/fake_backend.py` and in `backend/`. The refactor: move them into `common/` in one change, so that the fake and the real backend cannot drift apart on any of them.

### Backend work that stays on the event loop

argon2 verification at sign-in and the three per-request transactions (the token lookup, the touch, the route's own) run on the event loop. At a home lab's load they are not a cost; measure before moving them to the threadpool.

### Two small costs left in the backend

The double parent walk in `_check_write_target` (the containment check walks the components once, the file-or-folder check walks them again) and the scope test's per-route setup cost. Neither is worth a change of its own; fold either into a change that touches its code.

### An HTML twin of the northstar

A designed HTML twin of `northstar.md`, authored from it per the handbook's [`md-to-html.md`](https://github.com/ParkviewLab/handbook/blob/main/docs/md-to-html.md). In progress in the pull request that follows this one.
