<!--
SPDX-FileCopyrightText: 2026 Gary Frattarola <garyf@parkviewlab.ai>

SPDX-License-Identifier: MIT OR Apache-2.0
-->

# paper-boxing: hand-off brief

Status: approved; v1 under construction since 2026-09-17 at Gary's go-ahead. Written 2026-09-15 and brought up to handbook v0.19.0 on 2026-09-17. This text is the workers' brief and is committed to the repo as `docs/design.md` in the scaffold pull request. Questions still open are collected at the end; none blocks the scaffold.

## 1. What paper-boxing is

paper-boxing is a small local site manager for a home lab: one person, a private LAN, plain HTTP, running in Docker on the home-lab host. BookStack stays the wiki; its pages link to the sites that paper-boxing serves.

What it is for, in Gary's words: "a tool we use in the early part of designing and spec'ing out big new projects". Early design and specification produce documents (designed HTML pages, visual explorations, specifications, often with scripts) before a project has a repository or a release, and while they are still changing daily. Those documents need a place to live that is private, on the LAN, instant to update, and writable by agents as well as by a person. Once a project has a public repository and ships releases, its documentation moves to that repository's GitHub Pages site under the handbook's `docs-site.md`; paper-boxing is where a project's documents live before that point, and where anything private lives permanently.

- A person signs in to a simple web UI. There they create and delete named sites and upload, list, download, replace and delete each site's files, one file at a time; the page may let a person pick several files at once and upload them in turn, a convenience of the page and not a capability of the server.
- Each site is a folder of static files, served unchanged over HTTP at a stable address such as `http://<host>:35841/<site>/`.
- An agent holding an access token does the same work through paper-boxing's MCP server.

paper-boxing is not a wiki, a CMS, a build system or a reverse proxy. It runs nothing that it serves on the server side. It keeps no version history. It is not designed for the public internet or for mutually untrusted users. Safeguards against accidents are part of it; measures against strangers are not.

Where it lives:

- Repository: `ParkviewLab/paper-boxing`, public, following the ParkviewLab handbook (v0.19.0) in full, including the after-the-merge rules in `branching.md` and, optionally later, publishing its own `docs/` on GitHub Pages per `docs-site.md` (not part of v1).
- Licence: `MIT OR Apache-2.0`.
- Language: Python 3.13.
- Deployment to the home-lab host is a separate, later step outside this repository, with its secrets held in that deployment's secret store.

## 2. Components

| Component | Image | Responsibility |
|---|---|---|
| Backend (FastAPI) | `ghcr.io/parkviewlab/paper-boxing-backend` | The only writer of files and of the database, and the one implementation of every rule (paths, overwrite and delete intent, token scopes). It serves the REST API, authentication, and site and file operations. |
| Frontend (NiceGUI) | `ghcr.io/parkviewlab/paper-boxing-frontend` | The UI for people. It holds no files and no database, and calls the backend's REST API for everything. |
| MCP server | `ghcr.io/parkviewlab/paper-boxing-mcp` | The interface for agents: a standard ParkviewLab MCP server that calls the backend's REST API for everything. Decided by Gary on 2026-09-15; section 4a. |
| Site server | stock `nginx:stable-alpine` | Serves `/data/sites`, mounted read-only, on its own port. Decided by Gary on 2026-09-15: nginx rather than the backend, so that static serving is proven, cannot modify files, keeps running through backend restarts, and stays apart from the process that holds the data. |

Data lives in one volume, `/data`:

- `/data/sites/<slug>/…` is the served tree. It is the only path mounted into nginx.
- `/data/staging/` holds uploads in progress. It sits on the same filesystem as `sites/`, so replacing a file by rename is atomic.
- `/data/paper-boxing.sqlite3` holds users, sessions, access tokens, and site metadata (display name, creation time).

Ports, decided by Gary on 2026-09-15, in the home-lab host's 358xx block for our own services (3584x was unused by every published container on 2026-09-15; the deployment confirms nothing outside Docker listens there):

| Port | Service | Notes |
|---|---|---|
| 35840 | frontend (UI) | the address people open |
| 35841 | site server | part of every link placed in BookStack, `http://<host>:35841/<site>/`; never to change |
| 35842 | MCP server | for agents; `<host>:35842` goes in its transport-security allowlist |
| 35843 | backend REST API | published so agents can send files larger than the MCP cap directly (my reading of Gary's "that's good"; he may still choose to keep it inside the stack) |

Each image listens on its own number inside the container too (frontend 35840, MCP 35842, backend 35843), following the handbook's same-number mapping. nginx listens on 80 inside and is mapped to 35841. `PAPER_BOXING_PUBLIC_SITES_URL` defaults to `http://127.0.0.1:35841` in code and is set to `http://<host>:35841` in the deployment.

## 3. Repository layout and packaging

The handbook assumes one package, one image and one version per repo, and says nothing about several images. Decided by Gary on 2026-09-15: option A, one distribution with a subpackage per component, and one version for all three images. It is the simplest practical shape for three images that are always built and deployed together, and it reuses the handbook's `pyproject.toml`, version guard and release workflow with only the changes listed here. Where this project settles a practice the handbook lacks, the handbook is updated to match (section 10).

```text
paper-boxing-develop/
├── pyproject.toml        one distribution "paper-boxing", one version, extras: backend, frontend, mcp
├── uv.lock
├── Dockerfile            one shared build stage, three targets: backend, frontend, mcp
├── docker-compose.yml    four services, one named volume
├── src/paper_boxing/
│   ├── common/           API schemas, REST client, shared helpers
│   ├── backend/          FastAPI app, storage, accounts and tokens
│   ├── frontend/         NiceGUI pages, sign-in, per-session state
│   └── mcp/              the handbook's MCP server layout
├── tests/                test_<module>.py units, contract/, integration/; each worker adds its own
├── docs/                 design.md, api.md, northstar.md, deployment.md, CONTRIBUTING.md, in-flight_ideas.md
└── .github/workflows/    the handbook's templates; release.yml builds the three targets in a matrix
```

- One distribution, `paper-boxing`, with one `pyproject.toml`, one version and one `uv.lock`. It uses the src layout `src/paper_boxing/` with the subpackages `backend/`, `frontend/`, `mcp/`, and `common/` (shared pydantic schemas and the REST client used by the frontend and the MCP server).
- Component boundaries are enforced by a test, `tests/test_import_boundaries.py`, which walks the source with `ast` and needs no extra dependency: `common` imports none of the three components; `backend`, `frontend` and `mcp` may import `common` but never each other.
- One version: every release builds all three images from the same commit with the same tags, so the frontend and MCP server always match the backend's API, and a deployment names one version for the whole stack.
- Dependencies: the shared ones are core. Each image installs only its own extra:
  - `backend`: fastapi, uvicorn, `starlette>=1.3.1`, argon2-cffi;
  - `frontend`: nicegui, httpx;
  - `mcp`: `mcp>=1.29,<2`, fastapi, uvicorn, `starlette>=1.3.1`, httpx.
- Entry points: `python -m paper_boxing.backend`, `python -m paper_boxing.frontend` and `python -m paper_boxing.mcp`, each also installed as a console script.
- The MCP subpackage follows the handbook's module layout from `mcp-server-conventions.md`: `config.py` (a pure leaf), `__main__.py`, `server.py`, `tools.py`, `schema.py`, and `permissions.py`. It serves Streamable-HTTP only, with no stdio transport, because it runs in the stack on the home-lab host for every machine on the LAN. The backend is a plain FastAPI service with the same `config.py` discipline.
- Docker:
  - One root `Dockerfile` in the handbook shape. A shared stage holds the uv python3.13 slim base and the dependency resolution. Three targets, `backend`, `frontend` and `mcp`, each run `uv sync --locked --no-dev --extra <component>` and set their own `EXPOSE`, `HEALTHCHECK` against `/health`, and `CMD ["uv", "run", "python", "-m", "paper_boxing.<component>"]`. Only `backend` declares `VOLUME ["/data"]`.
  - `.dockerignore` excludes the git metadata, the virtual environment, caches, tests, docs and the compose files, so a build context holds only what the Dockerfile copies.
- `docker-compose.yml` has four services (backend, frontend, MCP server, site server) and one named volume, as a copy-and-edit example in the handbook's compose shape, with settings read from `${…}` variables so the same file works in Portainer.
- The nginx configuration is inline in the compose file, as a Compose `configs:` entry with `content:` mounted at `/etc/nginx/conf.d/default.conf`. That keeps the stack a single file that can be pasted into Portainer's web editor. If the host's Portainer rejects inline configs, fall back to a bind-mounted file and document that.
- `release.yml` builds the three targets in a matrix into `ghcr.io/parkviewlab/paper-boxing-{backend,frontend,mcp}`, each with the tags version, major.minor and `latest`, for amd64 and arm64.
- Publishing: Docker images on GHCR only, decided by Gary on 2026-09-15. There is no PyPI or TestPyPI publishing. The template `release.yml` loses its `pypi` job, and its `changelog` job then depends on `gate` and `docker` only. `dev-release.yml`, if adopted, publishes only `:dev` images. No trusted publisher is configured. paper-boxing is designed to run in Docker, and its documentation covers Docker and Portainer only.

## 4. Backend

REST API, all under `/api/v1`. JSON throughout, except for file bodies.

Authentication:

- `POST /auth/login` takes `{username, password}` and returns a session token with its expiry. `POST /auth/logout` ends the session.

Sites:

- `GET /sites` returns, for each site: slug, display name, URL, creation time, file count and bytes.
- `POST /sites` takes `{name}`. The slug is derived from the name (lowercase letters, digits, hyphens), must be unique, and stays stable afterwards.
- `DELETE /sites/{slug}?confirm={slug}` deletes a site. Without the matching `confirm` it answers 400.

Files and folders:

- `GET /sites/{slug}/files?path=` lists one folder: name, type, bytes, modification time and sha256.
- `PUT /sites/{slug}/files/{path}?overwrite=false` uploads one file as a raw body, creating missing parent folders on the way. An existing file without `overwrite=true` answers 409. The response is `UploadResult {path, bytes, sha256, replaced}`: 201 with `replaced: false` for a new file, 200 with `replaced: true` for a replacement.
- `GET /sites/{slug}/files/{path}` downloads a file as an attachment.
- `DELETE /sites/{slug}/files/{path}` deletes a file.
- `DELETE /sites/{slug}/folders/{path}?recursive=false` deletes a folder. A non-empty folder without `recursive=true` answers 409.

File operations are single-file only, decided by Gary on 2026-09-17: there is no batch upload, no folder upload and no explicit folder creation, because an empty folder has no meaning in a static site. Designed pages are self-contained, so a site is a handful of files; an agent that wants a site to match a new build lists, uploads what changed and deletes what is gone, which is a client-side loop, and the transient mixed state during such a loop is accepted for one reader on a private LAN. An all-or-nothing replace can be added later as one additive route if a need appears. The scaffold also added `GET /sites/{slug}`, one site's record, for the frontend's site page.

Access tokens and users:

- `GET /tokens` lists every user's agent tokens with their owner, and any user may revoke any of them (equal rights); `POST /tokens {name, scope}` (the token is shown once); `DELETE /tokens/{id}`.
- `GET /tokens/self` returns the calling token's id, name, type, scope, username and expires_at (null for an agent token, set for a session). The MCP server uses it to confirm each agent token (section 4a).
- `GET /users`, `POST /users`, `DELETE /users/{id}` (409 for the last account), and `POST /users/self/password {current, new}`.

The handbook's standard endpoints: `GET /health` returning `{ok, version, uptime_seconds}`, `GET /admin/version`, and `GET /docs`.

Authentication model (the handbook knows only one shared static token; this model is a candidate handbook update, section 10):

- Accounts (decided by Gary on 2026-09-15: several accounts supported):
  - Every account has the same rights; there are no roles.
  - The first account is created from `PAPER_BOXING_ADMIN_USERNAME` and `PAPER_BOXING_ADMIN_PASSWORD` when no account exists yet; after that those variables are ignored.
  - A signed-in user adds and removes accounts. The last remaining account cannot be deleted. There is no self-registration.
  - Each user changes their own password, which requires the current one. The change signs out that user's other sessions.
  - Each agent token belongs to the user who created it, and the logs record which user or token acted.
  - Passwords are stored only as argon2id hashes (`argon2-cffi`, per-hash random salt, cost settings in the encoded string, re-hashed at sign-in when the settings have been raised). A password is never logged, stored in plain text, or returned. Tokens are 32 random bytes, presented as `pb_` followed by 43 URL-safe base64 characters, and stored as SHA-256 hashes.
  - No lockout after failed sign-ins, and sign-in runs over plain HTTP; both follow from the private-LAN threat model and are stated in the README.
- Sessions:
  - A login issues a random session token (32 bytes). Only its SHA-256 is stored.
  - Sessions expire after a sliding period (configurable, default 14 days).
- Access tokens:
  - A signed-in user creates access tokens, and each token has a scope from the handbook's tiers: `read_only` (list and download), `read_write` (adds create site and upload), and `remove_destructive` (adds every delete).
  - Tokens are stored as SHA-256 hashes, can be revoked, and record when they were last used.
  - Every token has a type: `session` (issued at login, for the UI) or `agent` (created by a user, for agents). Only `agent` tokens are accepted through the MCP server.
- Every REST route requires `Authorization: Bearer <session or access token>`, except `/health`, `/admin/version` and `/docs`. Comparisons are constant-time.
- Managing accounts and tokens is a person's act: `POST /auth/logout`, the token routes and the user routes take a session token only, and an agent token gets `403 session_required`. A session token acts with its user's full rights, `remove_destructive`.
- The backend enforces every scope itself, whichever client calls. An agent token may also call the REST API directly, which is the route for files larger than the MCP cap.
- A request carrying `X-Paper-Boxing-Via: mcp` is logged as coming through the MCP server, under the token that acted.
- The README states plainly that on a private LAN over plain HTTP, tokens and passwords travel unencrypted. That is the accepted deployment model, and the README answers the handbook's "Auth model" warning with it.

Safeguards against accidents:

- Path containment: resolve the path, then check it still lies inside the site, as `safe_subpath` does in deco-assaying's `outputs.py`. This rejects `..`, absolute paths, encoded traversal and symlinks. Uploaded symlinks and special files are refused.
- Uploads stream into `/data/staging` with a size cap (`PAPER_BOXING_MAX_UPLOAD_MB`, default 200), then `os.replace` into place. A failure cleans the staging file up and leaves the old file whole. The patterns are in flint-slating's `pdf_source.py` and ebony-enriching's `storage/markdown.py`.
- Writes to the same path are serialized, following ebony-enriching's `mutex.py`.
- Disk-full and quota errors return clear responses and leave nothing half-written.

Configuration uses environment variables only, in the handbook's style (dataclass plus `os.environ.get`). `HOST` defaults to `127.0.0.1` in code and is set to `0.0.0.0` in the image. The other variables are `PORT`, `PAPER_BOXING_DATA_DIR`, `PAPER_BOXING_PUBLIC_SITES_URL`, the admin bootstrap pair, the size cap and `PAPER_BOXING_SESSION_DAYS`. All are documented in the README's configuration table.

## 4a. MCP server

Gary decided on 2026-09-15 that the MCP server is its own image and process, calling the backend's REST API, rather than being built into the backend. The reasons: the backend stays the one implementation of every rule, and the MCP server is a client of it, exactly as the frontend is; the MCP image is a standard ParkviewLab MCP server in the shape `mcp-server-conventions.md` describes; and upgrades of the MCP SDK (pinned below 2.0, whose 2.0 release requires code changes) never touch the process that writes files.

Structure:

- Built on the low-level `mcp` SDK Server with `StreamableHTTPSessionManager(stateless=True)`, mounted at `/mcp`, POST only. GET and DELETE answer 405, and the old `/sse` path answers 405 naming `/mcp`.
- Transport security is on, following ebony-enriching: `PAPER_BOXING_MCP_ALLOWED_HOSTS` and `PAPER_BOXING_MCP_ALLOWED_ORIGINS`, with CORS limited to the same allowlist.
- `GET /health`, `GET /admin/version` and `GET /docs`.

The three safeguards against token passthrough, from the MCP specification's security best practices:

1. Every request's bearer token is confirmed with the backend (`GET /api/v1/tokens/self`) before any tool runs. The MCP server keeps no token store of its own and caches nothing beyond the one request.
2. Only tokens the backend issued are accepted. An unknown, revoked or expired token gets 401 with no tool run.
3. Only tokens of type `agent` are accepted. A UI session token gets 403.

The confirmed token is forwarded to the backend on each call with `X-Paper-Boxing-Via: mcp`. The backend enforces the scope again and logs the call under that token.

Tools, each with a title, annotations and an `outputSchema`, failures raised as `ToolError`, and listed or refused according to the confirmed token's scope:

- `list_sites`, `create_site`, `list_files`
- `upload_file(site, path, content_base64, overwrite)`
- `download_file(site, path)`, returning base64, sha256 and a content type
- `delete_file`, `delete_folder(site, path, recursive)`, `delete_site(site, confirm)`

The MCP file size cap is configurable, default 8 MiB. Larger files go through the REST `PUT` and `GET` with the same token, and the tool descriptions say so.

Configuration: `HOST`, `PORT`, `PAPER_BOXING_BACKEND_URL`, `PAPER_BOXING_PUBLIC_SITES_URL`, `PAPER_BOXING_MCP_MAX_FILE_MB`, the allowlists above, and `PAPER_BOXING_MCP_ENABLE_TRANSPORT_SECURITY`.

## 5. Frontend

Pages:

- `/login`.
- `/`, the sites list: create a site, copy its URL, open it, and delete it after typing its slug, which the page shows.
- `/sites/{slug}`, one site:
  - browse folders;
  - upload files, with an overwrite choice: a person may pick several files at once and the page uploads them in turn against the single-file route, a convenience of the page and not a capability of the server;
  - download a file;
  - replace a file;
  - delete a file, or a folder with an explicit recursive confirmation.
- `/tokens`: create a token with a scope, shown once with a copy button; list tokens; revoke one.
- `/users`: list accounts, add an account, remove one (never the last).
- `/account`: change your own password.

Several simultaneous sessions must work correctly. This is a stated requirement, and the tests prove it (section 7):

- Every page is built inside its `@ui.page` function for the connecting client. No UI element, and no per-user or per-session value, lives at module level.
- A user's backend session token is kept in `app.storage.user`, which needs `PAPER_BOXING_STORAGE_SECRET`. Per-tab state goes in `app.storage.tab`. Middleware sends an unauthenticated request to `/login`.
- The one shared object allowed is a stateless `httpx.AsyncClient`, held in the app lifespan. Each call passes the current user's token explicitly.
- Long operations run with `await` or `run.io_bound`, so one user's upload never blocks another's UI.

Configuration: `HOST`, `PORT`, `PAPER_BOXING_BACKEND_URL`, `PAPER_BOXING_STORAGE_SECRET`, `PAPER_BOXING_PUBLIC_SITES_URL`.

## 6. Site server

The nginx configuration is short:

- `root /data/sites`, the served tree inside the read-only volume mount;
- `index index.html`, and `autoindex on` with `autoindex_exact_size off` and `autoindex_localtime on`, so a site or folder with no `index.html` of its own shows nginx's own directory listing (decided by Gary on 2026-09-17, closing open question 10): during early design a site is a few hand-made pages, and a bare list of files is what a person wants before an index exists; it costs one directive and no code;
- `try_files $uri $uri/ =404` stays, so a missing path is still a 404;
- `absolute_redirect off`. Without it, nginx's redirect from `/<site>` to `/<site>/` names its internal port 80 instead of the published 35841, and the redirected link breaks;
- `add_header Cache-Control "no-cache"`, so a replaced file shows at once, still revalidated through ETag;
- the default MIME types plus `.mjs` and `.webmanifest`.

It mounts the data volume read-only, with `root` pointing at its `sites/` tree; nothing outside that tree is reachable over HTTP. A site's root serves its `index.html`, or the listing when there is none. Links relative to the site work; links starting from the host root such as `/css/x.css` do not, and the README says so.

## 7. Tests

Handbook conventions: pytest with `asyncio_mode = "auto"`; markers `network`, `integration`; CI runs `-m "not network and not integration"`, and a separate job runs `integration`; one session-scoped client, since the session manager refuses to start twice. Reference files are ebony-enriching's `tests/{conftest,_mcp_helpers,test_server,test_transport_security,test_parallel}.py`.

Backend:

- traversal and symlink refusal;
- an interrupted replace, simulated by a failure during the write, leaves the old file intact;
- the 413, 409 and 400 intent guards;
- scope enforcement per tool and per route;
- tokens and passwords are never stored in plain text;
- session expiry;
- `GET /tokens/self` returns the right type and scope, and refuses revoked tokens and expired sessions.

MCP server (against a fake backend implementing the contract, and in the integration job against the real one):

- the three safeguards: an unknown, revoked or expired token gets 401 and no tool runs; a session token gets 403; the token is confirmed on every request, with nothing cached across requests;
- tools listed and refused according to scope;
- round trips of a text file and a PNG, compared by sha256;
- tool output checked against each `outputSchema`;
- `/mcp` GET and DELETE answer 405;
- transport security answers 421 and 403.

Frontend:

- With `nicegui.testing`'s `User` fixture: the login redirect, and two users signed in at the same time who never see each other's session (name or token), whilst the sites and the agent tokens, which the contract makes shared with equal rights, are listed to both.
- A token is shown only once.
- Upload flows, run against a fake backend that implements the contract in `common/`.

Integration (`integration` marker):

- `docker compose up` for the four services;
- `/health` on backend, frontend and MCP server;
- create a site and upload a small tree (HTML, CSS, JS, SVG, PNG, WOFF2) through the API and through MCP;
- fetch everything through nginx, byte-identical and with the right content types;
- the PensaForma design page as a real-world file: stored, served, identical.

Visual checks follow the handbook's rule: run the app and take screenshots before claiming a UI change works.

## 8. Handbook conventions used

- The contained worktree layout under `~/dev/github/ParkviewLab/paper-boxing/`.
- `develop` as the default branch and the pull-request target; `main` for releases; squash-only merges with the title as the commit title.
- Branch protection: required checks on `develop` with admin bypass; `main` protected only against force-push and deletion.
- Pyproject tool settings from the template.
- `cliff.toml` verbatim, and `scripts/generate_changelog.py`.
- Workflows `reuse.yml`, `version-guard.yml`, `test.yml`, `release.yml` (matrix), `license-check.yml`, and optionally `dev-release.yml`. Actions pinned exactly as `ci.md` lists. `version-guard.yml` departs from the template in one respect: it passes when the base branch has no version file, because a first introduction is not a bump (section 10, item 6).
- Licensing copied from deco-assaying, not from the handbook's AGPL templates:
  - `LICENSE-MIT`, `LICENSE-APACHE`, `LICENSES/MIT.txt` and `LICENSES/Apache-2.0.txt`;
  - a `REUSE.toml` with one annotation;
  - `LICENSING.md`, which lets the recipient choose either licence;
  - SPDX headers `MIT OR Apache-2.0` on every file;
  - `license-files` listing both licence files;
  - a README licence section.
- Docs: a README in the house shape; `docs/CONTRIBUTING.md`; `docs/in-flight_ideas.md`; `docs/design.md`, which is this brief; copyright footers.
- `docs/deployment.md`, required by Gary: how to build the stack and how to run it in Portainer. It is written for any reader of the public repo, and names no private host, token or secret-store path. It covers:
  - the four services, the images and their tags, and building the images locally (`docker build --target …`, `docker compose build`);
  - the compose file explained service by service: ports, the data volume and a bind-mount alternative, and every variable, with the secrets marked;
  - running with `docker compose`;
  - running in Portainer:
    - creating the stack from the web editor or from the repository;
    - entering the variables as Portainer stack environment variables;
    - first start and first sign-in;
    - updating to a new version by changing the tag and updating the stack;
    - reading logs and health;
  - creating an agent token and connecting an MCP client (for example `claude mcp add --transport http` with the `Authorization` header);
  - backing up and restoring `/data`, with a consistent copy of the SQLite file (`sqlite3 … ".backup …"`) rather than copying it while it is in use;
  - a short troubleshooting section: a 421 from the MCP server means the host is missing from its allowlist; a redirect that loses the port means `absolute_redirect` is wrong.

  The README's run section covers Docker and Portainer only, and links to this document.
- AI pointer files written by the handbook's `scripts/sync-agent-files.sh`, run against the develop worktree by explicit path.

## 9. Work plan and workers

The coordinator's setup of the repository is done (the public repo with `develop` as its default branch, squash-only merges, the protection of `main`, the initial commit `89ebea7`); branch protection on `develop` follows the scaffold's merge, once the required checks exist.

Pull requests into `develop`, each from its own prefixed worktree and each merged by Gary:

1. `build-scaffold` (one worker): the package skeleton with `/health` on all three services; config; licensing; workflows including the image matrix; Dockerfiles, compose and the nginx configuration; docs; pointer files. It also fixes the API contract: the `common/` pydantic schemas and REST client, the REST route table, a fake backend for tests, and the MCP tool specifications.
2. Once the scaffold is merged, three workers in parallel, each a background session in its own worktree, all built against the contract from PR 1:
   - `feature-backend`: storage, authentication, sessions and tokens, REST, backend tests.
   - `feature-frontend`: all pages, the multi-session design, frontend tests against the fake backend.
   - `feature-mcp`: the MCP server, its three safeguards, tools, MCP tests against the fake backend.
3. `test-integration`, after all three have merged: the compose-based integration job, and `docs/deployment.md` completed from instructions the job has actually run (the scaffold PR drafts it).

After these, the first release (`git bump`, `git release`), at Gary's per-action go-ahead. Deployment to the home-lab host then gets its own brief, outside this repository.

Workers are started as `parallel-work.md` prescribes, with the handbook passed by `--add-dir`: `--agent coder --model fable --effort max --permission-mode auto`. Max effort is Gary's standing preference at home. If Fable's usage limit stops a run, as it did on 2026-09-15, the worker restarts on `--model opus` at max effort from its pushed state, which is the fallback Gary chose.

The stopping rule in every brief:

- local checks green;
- push after each commit;
- the version file never touched;
- no merge and no pull request;
- a final report that proposes the PR title, with its Conventional Commit prefix, and the body.

The coordinator verifies each branch independently, opens the PR, and relays it to Gary.

## 10. Handbook updates this project brings

The handbook is a working guide that evolves with practice (Gary, 2026-09-15). paper-boxing does what is practical. Where it settles a practice the handbook lacks or gets wrong, the handbook is updated to match. The updates are written in the handbook's own `docs/handbook-improvements_ideas.md` as paper-boxing proves each practice, and proposed as a handbook pull request once they have worked in a release:

1. A Docker-only service profile: images on GHCR with no PyPI job and no trusted publishers, a README run section covering Docker and Portainer only, and a `docs/deployment.md` for Portainer.
2. Repos that build several images from one distribution: subpackages with per-image extras, one Dockerfile with a target per image, a release matrix, `-<component>` image suffixes, one version, and an import-boundary test.
3. An MCP server that calls its own backend: Streamable-HTTP only, with per-user typed tokens confirmed with the backend on every request, as an alternative to the single shared token.
4. Web frontends with NiceGUI: per-client page construction, `app.storage.user` for per-user state, an authentication middleware, and multi-user tests with `nicegui.testing`.
5. Serving user-supplied static sites with a stock nginx container whose configuration lives inline in the compose file.
6. A version guard that passes when the base branch has no version file: a first introduction of `pyproject.toml` (or `package.json`, or `VERSION.txt`) is not a bump. The template compares against an empty string there and fails the scaffold pull request of every new repository.

## Decisions

Made by Gary on 2026-09-15: ParkviewLab/paper-boxing, public; `MIT OR Apache-2.0`; Python 3.13; a FastAPI backend and a NiceGUI frontend that handles several sessions at once; the MCP server as a separate image calling the backend, with the three safeguards against token passthrough.

Gary's questions, to be settled with him one at a time, in this order:

1. What serves the site files. Decided: a stock nginx container, mounting the sites read-only.
2. One account or several. Decided: several accounts with equal rights.
3. The ports. Decided: 35840 UI, 35841 sites, 35842 MCP, 35843 backend REST API.
4. One version or several for the images. Decided: one version.
5. How the repo's layout accommodates the images. Decided: option A, one distribution with subpackages, one Dockerfile with three targets, an import-boundary test.

Also required by Gary: `docs/deployment.md`, explaining how to build the stack and run it in Portainer (section 8).

Also open, after those:

6. PyPI or images only. Decided: Docker images on GHCR only.
7. Work split. Decided: option 1, in phases: scaffold; then backend, frontend and MCP in parallel; then integration (section 9).
8. A `docs/northstar.md`. Decided: drafted in the scaffold PR, for Gary's editing. It opens with what paper-boxing is for (the working space for the early design and specification of big new projects, before a repository and a release, and for private documents permanently), then three intents: pages kept exactly as written; equally usable by a person and an agent through one backend; sized for one person's home lab in Docker; plus what it is not (not a wiki, a CMS, a build system, a public host, or a replacement for a released project's GitHub Pages site). No HTML twin until paper-boxing is up and running.

Decided on 2026-09-17, during the scaffold:

10. What a site with no `index.html` of its own shows at its root. Decided: nginx's own listing (`autoindex on`); section 6 records the reason.
11. File operations are single-file only: no batch upload, no folder upload, no explicit folder creation; section 4 records the reason.

Still open, for Gary, before the backend and frontend workers start (it does not block the scaffold):

9. Whether paper-boxing also previews a repository's documentation site before its release (the build script's output uploaded as a site). No new capability is needed; it is a question of intended use.
