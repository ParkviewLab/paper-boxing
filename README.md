<!--
SPDX-FileCopyrightText: 2026 Gary Frattarola <garyf@parkviewlab.ai>

SPDX-License-Identifier: MIT OR Apache-2.0
-->

# paper-boxing

A small site manager for a private home lab: named sites of static files served unchanged on the LAN, a web UI for people, and an MCP server for agents, all in Docker.

Documentation: <https://parkviewlab.github.io/paper-boxing/> (the newest release's documentation)

paper-boxing is where a project's documents live during early design and specification, before its first release, where a project that has a repository previews its documentation site before the release that publishes it (the site build's output uploaded as a site), where an uploaded page, generated on another machine, reports the state of running systems, and where anything private lives permanently. Designed HTML pages, visual explorations, specifications: uploaded by a person or by an agent, served at a stable address such as `http://<host>:35841/<site>/`, replaced in place as they change. Several people can have accounts, all of equal standing: any account may create sites, tokens and other accounts, and may remove any of them, except the last account. There are two kinds of token: a session, issued when a person signs in to the UI, and an agent token, which a signed-in person creates in the UI with a scope; an agent is any MCP client holding one, such as a Claude Code session.

## Status

Released; the newest version is on the [releases page](https://github.com/ParkviewLab/paper-boxing/releases/latest), and the three paper-boxing images are on GHCR, tagged as [Releasing](#releasing) describes. The backend's REST API, the frontend's pages and the MCP server's tools share the contract in [`docs/api.md`](docs/api.md), and an integration tier runs the four-container stack end to end on every pull request. The architecture is [`docs/architecture.md`](docs/architecture.md), the record of decisions [`docs/decisions.md`](docs/decisions.md) and the intent [`docs/northstar.md`](docs/northstar.md).

Four containers make a deployment:

| Component | Image | Port | Role |
|---|---|---|---|
| backend | `ghcr.io/parkviewlab/paper-boxing-backend` | 35843 | the REST API; the only writer of files and of the database |
| frontend | `ghcr.io/parkviewlab/paper-boxing-frontend` | 35840 | the web UI for people |
| mcp | `ghcr.io/parkviewlab/paper-boxing-mcp` | 35842 | the MCP server for agents |
| sites | `nginx:stable-alpine` | 35841 | serves the sites, read-only |

All three paper-boxing images are built from one commit and carry one version.

## Run

paper-boxing runs in Docker, either with `docker compose` or as a Portainer stack; there is no PyPI package. [`docs/deployment.md`](docs/deployment.md) is the full guide: the compose file service by service, every variable, first sign-in, updating, backing up, connecting an MCP client, building the images from a checkout.

`.env` holds, among the variables the Configuration tables below describe, the admin pair (`PAPER_BOXING_ADMIN_USERNAME` and `PAPER_BOXING_ADMIN_PASSWORD`, from which the backend creates the first account when none exists) and the allowed hosts (`PAPER_BOXING_MCP_ALLOWED_HOSTS`, the `Host` values with which agents connect to the MCP server).

```bash
cp .env.example .env          # set the public sites URL, the admin password, the storage secret and the MCP allowed hosts
docker compose up -d
docker compose ps             # four containers; frontend and mcp show "health: starting" for a few seconds; nginx has no health check
curl http://127.0.0.1:35843/health
```

Then open `http://<host>:35840/` and sign in with the admin pair from `.env`. For Portainer, paste [`docker-compose.yml`](docker-compose.yml) into the stack's web editor and enter the same variables as the stack's environment variables; [`docs/deployment.md`](docs/deployment.md) walks through it.

What the deployment model assumes, stated plainly: a home lab's private LAN, plain HTTP, no TLS. Passwords and tokens travel unencrypted between the browser or the agent and the stack; there is no lockout after failed sign-ins. Safeguards against accidents are part of paper-boxing (path containment, overwrite and delete intent, token scopes); measures against strangers are not, and it is not for the public internet.

Links inside a site work when they are relative to the site; a link from the host root such as `/css/x.css` does not, because every site lives under `/<site>/`.

## Endpoints

Backend, port 35843: the REST API under `/api/v1` ([`docs/api.md`](docs/api.md)), `GET /health`, `GET /admin/version`, `GET /docs`, `GET /openapi.json`.

Frontend, port 35840: the pages `/login`, `/` (sites), `/sites/<slug>` (with `?path=<folder>` for a folder), `/tokens`, `/users`, `/account`; `GET /download/<slug>/<path>`, which streams a file from the backend with the signed-in person's session, since the browser never holds the session token; `GET /health`, `GET /admin/version`. A request for a page without a session is sent to `/login` and back afterwards; a download without one gets a 401 in the API's error shape, never the sign-in page. The session lives in NiceGUI's per-browser storage on the frontend (`.nicegui/` under the working directory), so a re-created frontend container asks everyone to sign in again.

MCP server, port 35842: `POST /mcp` (Streamable HTTP; `GET` and `DELETE` answer 405), `/sse` (the old HTTP+SSE path, which answers 405 naming `/mcp`; [`docs/api.md`](docs/api.md#mcp-tools)), `GET /health`, `GET /admin/version`, `GET /docs`, `GET /openapi.json`.

Site server, port 35841: `GET /<site>/...`, the files as uploaded. A folder with no `index.html` shows nginx's listing. Source code, configuration, data and plain-text files, and a file with no extension or with an extension in no table, are served as `text/plain` so the browser displays them rather than downloading them; the groups are named in [`docs/architecture.md`](docs/architecture.md#the-site-server), the compose file's `types` block is the list, and `tests/_site_server_types.py` is the check on it.

`/health` returns `{ok, version, uptime_seconds}` on all three services.

## MCP tools

An agent connects with an agent token created in the UI; the token's scope decides which tools it sees and may call. Every request's token is confirmed with the backend before a tool runs; a UI session token is refused.

Read only (`read_only`):

- `list_sites()`: every site with its URL, file count and bytes.
- `list_files(site, path="")`: one folder, folders first then files, with sizes, modification times and sha256.
- `download_file(site, path)`: the file as base64 with its sha256 and content type.

Read and write (`read_write`), adds:

- `create_site(name)`: a site; the slug is derived from the name and never changes.
- `upload_file(site, path, content_base64, overwrite=false)`: one file, creating parent folders; an existing file is refused without `overwrite`.

Remove and destructive (`remove_destructive`), adds:

- `delete_file(site, path)`.
- `delete_folder(site, path, recursive=false)`: a non-empty folder needs `recursive`.
- `delete_site(site, confirm)`: `confirm` must equal the slug.

Tool calls carry files up to `PAPER_BOXING_MCP_MAX_FILE_MB` (default 8 MiB). A larger file goes through the REST API with the same token: `PUT` and `GET /api/v1/sites/{slug}/files/{path}`.

## Configuration

Every variable is read from the environment at startup. All three services read `HOST`, the bind address, which defaults to `127.0.0.1` in code and is set to `0.0.0.0` by the images, and `PORT`, each service's own number.

Backend:

| Variable | Default | Purpose |
|---|---|---|
| `PORT` | `35843` | listen port |
| `PAPER_BOXING_DATA_DIR` | `./data` (image: `/data`) | the volume: `sites/`, `staging/`, `paper-boxing.sqlite3` |
| `PAPER_BOXING_PUBLIC_SITES_URL` | `http://127.0.0.1:35841` | the site server's address as people and links reach it; every site's URL is built from it |
| `PAPER_BOXING_ADMIN_USERNAME` | unset in code; `admin` in the compose file | the first account, created only when no account exists, under the rules of any account (`POST /api/v1/users` in [`docs/api.md`](docs/api.md#routes)) |
| `PAPER_BOXING_ADMIN_PASSWORD` | unset | its password, under the same rules as any account's ([`docs/api.md`](docs/api.md#routes)); ignored once an account exists. While no account exists, an invalid pair stops the backend from starting, and an unset pair lets it start with a logged warning and nobody able to sign in |
| `PAPER_BOXING_MAX_UPLOAD_MB` | `200` | size cap of one uploaded file, in MiB; at least 1 |
| `PAPER_BOXING_SESSION_DAYS` | `14` | sliding expiry of a UI session, in days; at least 1 |

Frontend:

| Variable | Default | Purpose |
|---|---|---|
| `PORT` | `35840` | listen port |
| `PAPER_BOXING_BACKEND_URL` | `http://127.0.0.1:35843` | the backend (`http://backend:35843` in the stack) |
| `PAPER_BOXING_PUBLIC_SITES_URL` | `http://127.0.0.1:35841` | as for the backend |
| `PAPER_BOXING_STORAGE_SECRET` | unset, required | signs the per-user session cookie; the frontend refuses to start without it |

MCP server:

| Variable | Default | Purpose |
|---|---|---|
| `PORT` | `35842` | listen port |
| `PAPER_BOXING_BACKEND_URL` | `http://127.0.0.1:35843` | the backend |
| `PAPER_BOXING_PUBLIC_SITES_URL` | `http://127.0.0.1:35841` | as for the backend |
| `PAPER_BOXING_MCP_MAX_FILE_MB` | `8` | largest file a tool call carries, in MiB; larger files go through the REST API |
| `PAPER_BOXING_MCP_ENABLE_TRANSPORT_SECURITY` | `true` | Host and Origin validation (DNS-rebinding protection) on `/mcp` |
| `PAPER_BOXING_MCP_ALLOWED_HOSTS` | `localhost`, `127.0.0.1`, `[::1]`, each also with `:*` | comma-separated `Host` values accepted on `/mcp`; a deployment adds `<host>:35842` (or `<host>:*`). Another Host gets 421 |
| `PAPER_BOXING_MCP_ALLOWED_ORIGINS` | `http://localhost`, `http://127.0.0.1`, each also with the listen port (`:35842`) | comma-separated browser origins accepted on `/mcp` and for CORS; a non-browser client sends no Origin and passes. Another Origin gets 403 |

There is no shared static token: an agent authenticates with an agent token that a signed-in user created, and the backend enforces the token's scope on every call, whichever client made it.

## Releasing

Tag-driven via the `Release` workflow on push of a `v*` tag. Use the [`ParkviewLab/dev-tools`](https://github.com/ParkviewLab/dev-tools) helpers; `pyproject.toml` is the only place the version lives, and the workflow's gate refuses a tag that does not match it. A release runs from the `paper-boxing-main` worktree, in the handbook's flow:

```sh
git pull --ff-only                              # sync main
git -C ../paper-boxing-develop pull --ff-only   # sync develop: the merge below takes the local branch
git merge --no-ff develop                       # promote develop to main; the merge commit is the release ledger entry
git bump <patch|minor|major>                    # bumps pyproject.toml and commits "release vX.Y.Z"
git release                                     # annotated tag vX.Y.Z from pyproject.toml
git push --follow-tags                          # the tag push fires the workflow
```

Once the workflow is green, the back-merge cascade brings `main`'s release and changelog commits down: `main` into `develop` with `--no-ff` (`git -C ../paper-boxing-develop merge --no-ff main`), then `develop` into each open working branch, and `develop` opens the next development cycle (`X.Y.(Z+1).dev0` in `pyproject.toml`).

The workflow runs a **gate** (tag equals the version, tag reachable from `main`, version greater than the previous tag), then a **docker** matrix that builds and pushes the three images for amd64 and arm64 with the `X.Y.Z`, `X.Y` and `latest` tags, then a **changelog** job that writes the new [`CHANGELOG.md`](CHANGELOG.md) section (an LLM-written Highlights paragraph plus [`git-cliff`](https://git-cliff.org/)'s categorized list), commits it to `main`, and creates the GitHub Release. There is no PyPI publish.

### Commit message convention

PRs are squash-merged with the PR title as the commit subject, so the PR title carries the [Conventional Commit](https://www.conventionalcommits.org/) prefix that [`cliff.toml`](cliff.toml) reads: `feat:` and `fix:` and `perf:` are user-visible sections, `refactor:`, `docs:` and `test:` have their own, and `chore:`, `ci:`, `build:` and `style:` are dropped from the changelog but stay in history; merge commits are dropped as well, and a `Revert` commit goes to a Reverts section. See [`docs/CONTRIBUTING.md`](docs/CONTRIBUTING.md).

## License

Licensed under either of

- Apache License, Version 2.0 ([LICENSE-APACHE](LICENSE-APACHE) or <http://www.apache.org/licenses/LICENSE-2.0>), or
- MIT license ([LICENSE-MIT](LICENSE-MIT) or <http://opensource.org/licenses/MIT>)

at your option. In SPDX terms: `MIT OR Apache-2.0`.

Unless you explicitly state otherwise, any contribution intentionally submitted for inclusion in this work by you shall be dual-licensed as above, without any additional terms or conditions. See [LICENSING.md](LICENSING.md), which also names the two vendored assets outside this dual licence: the ParkviewLab brand files, all rights reserved, and the Michroma font, under OFL-1.1.

---
<sub>© 2026 Gary Frattarola · Licensed under [MIT](LICENSE-MIT) OR [Apache-2.0](LICENSE-APACHE) · part of [ParkviewLab](https://github.com/ParkviewLab)</sub>
