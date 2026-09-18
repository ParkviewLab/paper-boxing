<!--
SPDX-FileCopyrightText: 2026 Gary Frattarola <garyf@parkviewlab.ai>

SPDX-License-Identifier: MIT OR Apache-2.0
-->

# paper-boxing: decisions

This is the record of paper-boxing's decisions: dated entries that describe what was decided and why, kept as history. Entries stand in the order they were made, newest last, and an entry is not rewritten when a later decision changes it; the later entry records the change. Each entry names the decision, the reason as it was recorded, and the alternative set aside; where the record gives no reason or names no alternative, the entry says so rather than supplying one, and an entry taken in conversation rather than from a document says so. The architecture the decisions produce is described in [`architecture.md`](architecture.md); questions still open are in [`in-flight_ideas.md`](in-flight_ideas.md).

## 2026-09-15: nginx as the site server

Decided: the sites are served by a stock `nginx:stable-alpine` container that mounts the data volume read-only, on its own port, rather than by the backend process. Reason: static serving by nginx is proven; a separate read-only process cannot modify files, keeps running through backend restarts, and stays apart from the process that holds the data. Set aside: serving the site files from the backend.

## 2026-09-15: several accounts with equal rights

Decided: several accounts, all with the same rights and no roles; a signed-in user adds and removes accounts; the last remaining account cannot be removed; there is no self-registration; the first account is created from the admin variables when no account exists, and the variables are ignored afterwards; each agent token belongs to the user who created it, and the logs record which user or token acted. Reason: not recorded beyond the decision. Set aside: one account.

## 2026-09-15: the ports 35840 to 35843

Decided: 35840 for the frontend, 35841 for the site server, 35842 for the MCP server and 35843 for the backend's REST API, in that order, each paper-boxing image listening on its own number inside the container as well, and nginx on 80 inside, published as 35841. Reason: the 358xx block on the Docker host is kept for the lab's own services, and 3584x was unused by every published container, verified on that date. Set aside: none recorded.

## 2026-09-15: one version for the three images

Decided: the three images carry one version, and a deployment names one version for the whole stack. Reason: every release builds all three images from the same commit with the same tags, so the frontend and the MCP server always match the backend's API. Set aside: a version per image.

## 2026-09-15: one distribution and one Dockerfile with three targets

Decided: one distribution, `paper-boxing`, with one `pyproject.toml`, one version and one `uv.lock`, in the src layout with a subpackage per component (`backend`, `frontend`, `mcp`) and a shared `common`, an extra per image, one `Dockerfile` with a shared base stage and a target per image, a release workflow that builds the targets in a matrix, and a test, `tests/test_import_boundaries.py`, that keeps `common` free of the components and the components free of each other. Reason: it is the simplest practical shape for three images that are always built and deployed together, and it reuses the handbook's `pyproject.toml`, version guard and release workflow with few changes, where the handbook itself assumes one package, one image and one version per repository. Set aside: the alternatives are not enumerated in the record.

## 2026-09-15: images on GHCR only

Decided: paper-boxing is published as Docker images on GHCR and nothing else; there is no PyPI or TestPyPI publishing and no trusted publisher, the release workflow has no PyPI job, and its changelog job depends on the gate and the image build only. Reason: paper-boxing is designed to run in Docker, and its documentation covers Docker and Portainer only. Set aside: publishing the distribution on PyPI.

## 2026-09-15: a separate MCP image

Decided: the MCP server is its own image and process, a client of the backend's REST API, with three safeguards against token passthrough: every request's token is confirmed with the backend before a tool runs, only a token the backend issued is accepted, and only a token of the agent type is accepted. Reason: the backend stays the one implementation of every rule and the MCP server is a client of it, exactly as the frontend is; the image is a standard ParkviewLab MCP server in the shape the handbook describes; and an upgrade of the MCP SDK, pinned below 2.0 because its 2.0 release requires code changes, never touches the process that writes files. Set aside: an MCP endpoint built into the backend.

## 2026-09-15: agent tokens without expiry and management by session only

Decided: an agent token has no expiry; it lives until it is revoked or its owner's account is removed. Sign-out and the token and user management routes (list, create and revoke a token; list, create and remove an account; change a password) take a session token only, so an agent token on any of them is refused with `403 session_required`; a session token acts with its user's full rights. Reason: managing accounts and tokens is a person's act. The record gives no reason for the absence of an expiry beyond the decision. Set aside: not recorded.

## 2026-09-17: single-file operations

Decided: file operations are single-file only: no batch upload, no folder upload and no route that creates an empty folder; folders come into being with the first file below them. The UI may let a person pick several files at once and uploads them in turn against the single-file route, a convenience of the page and not a capability of the server. Reason: an empty folder has no meaning in a static site; designed pages are self-contained, so a site is a handful of files; an agent that wants a site to match a new build lists, uploads what changed and deletes what is gone, a client-side loop whose transient mixed state is accepted for one reader on a private LAN. Set aside: batch upload, folder upload and folder creation. An all-or-nothing replace, one additive route, stays an open idea in [`in-flight_ideas.md`](in-flight_ideas.md).

## 2026-09-17: the site server lists a folder that has no index page

Decided: a site or a folder with no `index.html` of its own shows nginx's own directory listing (`autoindex on`, with `autoindex_exact_size off` and `autoindex_localtime on`); the server's root, the `sites/` folder, lists the site folders as a consequence. Reason: during early design a site is a few hand-made pages, and a bare list of files is what a person wants before an index exists; it costs one directive and no code. Set aside: an index page of paper-boxing's making.

## 2026-09-17: the whole volume mounted read-only

Decided: the site server mounts the whole data volume read-only and nginx's `root` points at its `sites/` tree, rather than mounting `sites/` alone. Reason: a Compose volume `subpath` needs Docker 26 or newer, and the deployment host's version is not assured; with `root` at `/data/sites`, nothing beside that tree (the database, the staging area) is reachable over HTTP, and the mount works on every Docker version. Set aside: a volume `subpath` mount of `sites/` alone, which stays an open idea in [`in-flight_ideas.md`](in-flight_ideas.md) for when the host's Docker version allows it.

## 2026-09-17: the backend port published

Decided: the compose file publishes the backend's port, 35843, beside the frontend's, the site server's and the MCP server's. The discussion of 2026-09-15 left this open: the proposal met with assent, with keeping the port inside the stack still possible; the port shipped with the compose file on 2026-09-17. Reason: an agent can then send a file larger than the MCP tool-call cap straight to the REST API, with the same agent token, through `PUT` and `GET /api/v1/sites/{slug}/files/{path}`; nothing else needs the port from outside. Set aside: keeping the backend reachable only inside the compose network.

## 2026-09-17: the version guard passes a first introduction of the version file

Decided: when the base branch has no version file at all, `.github/workflows/version-guard.yml` passes and says that the file is new. Reason: a version file that is new in a pull request is introduced, not bumped; the handbook's template compares against an empty string there and fails the first pull request of every new repository. Set aside: the template's comparison as it stands.

## 2026-09-17: streaming in the frontend

Decided: the frontend's download route streams a file from the backend chunk by chunk into a `StreamingResponse` (`BackendClient.stream_file`), and an upload streams each file with its size declared as `Content-Length` (`BackendClient.upload_file` takes an async iterator); `BackendClient.download_file` stays as the whole-body form for the MCP server. Reason: a file near the backend's cap is never held whole in the frontend's memory nor hashed on the event loop. Set aside: whole-body transfer through the frontend.

## 2026-09-17: previewing a documentation site before its release

Decided, in conversation: previewing a repository's documentation site before the project's first release is a supported secondary use: the site build's output is uploaded as a site and read on the LAN as it will be published. Reason: no new capability is needed, since the output is a folder of files like any other; it is a question of intended use. Consequence: the northstar's scope reads "before its first release" rather than "before a repository and a release". Set aside: confining the intended use to projects that have no repository yet.

## 2026-09-17: the official horizontal logo in the header

Decided: the frontend's header carries the brand's white horizontal logo file on the brand's deep teal, scaled by cropping its `viewBox` to the artwork; the file is used as drawn, except that its Google Fonts import is replaced at render time with the same face, Michroma, embedded from the vendored woff2, so nothing is fetched from the network. Reason: the brand's own file as drawn rather than a rendition of paper-boxing's making, and the handbook's rule that Michroma is self-hosted and never imported from Google Fonts. Set aside: the logo-only mark drawn in one colour for the teal header beside a hand-set wordmark; the dark artwork on the brand's paper ground.

## 2026-09-17: the documentation site

Decided, in conversation: this repository's `docs/` is published as a GitHub Pages site in the shape the handbook's docs-site page describes, built from `main` by the shared build script, whose home the decision places in the `dev-tools` repository, rather than by a copy of the script kept here. Reason: the site is the released documentation's home, and paper-boxing is the second repository to adopt it, so the script is shared rather than copied. Set aside: a per-repository copy of the build script.

## 2026-09-17: HTML twins for two documents

Decided, in conversation: designed HTML twins are authored for exactly two documents, `northstar.md` and `architecture.md`; `api.md`, `deployment.md`, `decisions.md` and `in-flight_ideas.md` stay Markdown, reachable from the site through GitHub's rendered view pinned to the release tag. Reason: the handbook's rule that a twin is authored only where the visual channel carries meaning the prose cannot. Set aside: twins for the other four documents.

## 2026-09-17: a page generated elsewhere as a third use

Decided, in conversation: a page that reports the state of running systems rather than a project's design, generated on another machine and uploaded, is a supported third use: paper-boxing stores it and serves it exactly as it does a hand-made page. Reason: no capability is added, since such a page is a file like any other and the backend applies the same rules to it whoever uploads it; it is a question of intended use. Consequence: the northstar's "Why it exists" records a third use beside the documentation-site preview, the README enumerates it, and axiom 2 and the "Not a CMS or a build system" item now say "the one page generated on the server", which they had called "the one generated page of content", a phrase that read as false beside a page generated on another machine. Occasion: a proposed page reporting the state of the organisation's projects and of the lab's services, which is not built and which the northstar deliberately does not name. Set aside: confining the intended use to a project's own documents.

## 2026-09-17: source, configuration and text files served as text

Decided, in conversation: the site server serves source code, configuration, data and plain-text files as `text/plain` with `charset=utf-8` (Python, TypeScript and JSX, Rust, C and C++, Swift and Objective-C, other languages, shell and build files, configuration and data, documents and text, schemas and interface definitions, diagram sources, XML schemas and tooling, and robotics formats; the list, by group, is `tests/_site_server_types.py`), `.cjs` as `application/javascript` beside `.mjs`, and a file with no extension or with an extension in no table as `text/plain; charset=utf-8` too (`default_type`); the charset is written into the type, in the `types` block and in `default_type`, rather than set with nginx's `charset` directive, whose `charset_types` always includes `text/html` and would give every page, the listing and the 404 page a charset header, so the charset is carried by `text/plain` responses and by nothing else; `txt`, `ts`, `pl` and `pm` override the stock table's `text/plain`, `video/mp2t` and `application/x-perl` on purpose. Reason: the stock `mime.types` answers `application/octet-stream` for these, which makes the browser download a file a person wanted to read; a design site holds scripts, specifications and configuration beside its pages, and a `README`, `LICENSE`, `Makefile` or `Dockerfile` has no extension. The bytes served are unchanged; only the header is, so the page as served stays the page as uploaded. Accepted cost: a binary file with an unknown extension shows as garbage instead of downloading. Set aside: keeping the stock table, with a download for everything it does not know; a charset on every text type, which could override a page's own declaration.

---
<sub>© 2026 Gary Frattarola · Licensed under [MIT](../LICENSE-MIT) OR [Apache-2.0](../LICENSE-APACHE) · part of [ParkviewLab](https://github.com/ParkviewLab)</sub>
