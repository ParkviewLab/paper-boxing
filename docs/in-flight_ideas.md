<!--
SPDX-FileCopyrightText: 2026 Gary Frattarola <garyf@parkviewlab.ai>

SPDX-License-Identifier: MIT OR Apache-2.0
-->

# In-flight ideas

Scratchpad for ideas under consideration: questions, not commitments (see the handbook's `documentation.md`). Don't act on an entry silently. The design's open questions live at the end of [`design.md`](design.md); the entries here point at them.

## Open

### Previewing a repository's documentation site before its release (design question 9)

Whether paper-boxing also serves as the preview of a project's `docs/` site before the project's first release: the handbook's docs-site build script's output uploaded as a site. No new capability is needed; it is a question of intended use, and of whether the northstar's "before a repository and a release" should read "before a release". For Gary, before the backend and frontend workers start; it does not block the scaffold.

### An all-or-nothing site replace

File operations are single-file only (design section 4, decided 2026-09-17). If a need appears for a site to switch from one build to the next with no transient mixed state, it would be one additive route (upload a tree to staging, then swap the site's folder by rename), not a change to the existing ones. Wait for the need.

### A volume subpath for the site server

The stack mounts the whole data volume read-only into nginx and points `root` at its `sites/` tree, because a Compose `volume.subpath` needs Docker 26 or newer and the deployment host's version is not assured. Once it is, the mount can narrow to `sites/` alone; nothing else changes.

### Publishing `docs/` as a site

The handbook's `docs-site.md` (a GitHub Pages site built from `main`) is not part of v1 (design section 1). Revisit after the first release.

### `dev-release.yml`

The handbook's on-demand dev build, publishing `:dev` images only (design section 3). Not adopted in the scaffold; adopt if a candidate needs exercising on the host before a release.

### An HTML twin of the northstar

Decided in the design (decision 8): no HTML twin until paper-boxing is up and running. Then author one from `northstar.md` per the handbook's `md-to-html.md`.

## Decided

### What a site with no `index.html` shows (design question 10)

Decided by Gary on 2026-09-17: nginx's own listing (`autoindex on`, with `autoindex_exact_size off` and `autoindex_localtime on`). Design section 6 records the reason. Closed.
