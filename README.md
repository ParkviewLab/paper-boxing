<!--
SPDX-FileCopyrightText: 2026 Gary Frattarola <garyf@parkviewlab.ai>

SPDX-License-Identifier: MIT OR Apache-2.0
-->

# paper-boxing

A small site manager for a private home lab: named sites of static files served unchanged on the LAN, a web UI for people, and an MCP server for agents, all in Docker.

paper-boxing is where a project's documents live during early design and specification, before the project has a repository and a release, and where anything private lives permanently. Designed HTML pages, visual explorations, specifications: uploaded by a person or by an agent, served at a stable address such as `http://<host>:35841/<site>/`, replaced in place as they change.

## Status

Under construction. This is the scaffold: the package skeleton, the API contract, the images and the stack. The backend's REST API, the frontend's pages and the MCP server's tools are being built against the contract in [`docs/api.md`](docs/api.md); the design is [`docs/design.md`](docs/design.md) and the intent [`docs/northstar.md`](docs/northstar.md).

Four containers make a deployment:

| Component | Image | Port | Role |
|---|---|---|---|
| backend | `ghcr.io/parkviewlab/paper-boxing-backend` | 35843 | the REST API; the only writer of files and of the database |
| frontend | `ghcr.io/parkviewlab/paper-boxing-frontend` | 35840 | the web UI for people |
| mcp | `ghcr.io/parkviewlab/paper-boxing-mcp` | 35842 | the MCP server for agents |
| sites | `nginx:stable-alpine` | 35841 | serves the sites, read-only |

All three paper-boxing images are built from one commit and carry one version.

## Run

paper-boxing runs in Docker, either with `docker compose` or as a Portainer stack; there is no PyPI package. [`docs/deployment.md`](docs/deployment.md) is the full guide: building the images, every variable, first sign-in, updating, backing up, connecting an MCP client.

```bash
cp .env.example .env          # set the two secrets and the public sites URL
docker compose up -d
curl http://127.0.0.1:35843/health
```

For Portainer, paste [`docker-compose.yml`](docker-compose.yml) into the stack's web editor and enter the same variables as the stack's environment variables.

What the deployment model assumes, stated plainly: one person's private LAN, plain HTTP, no TLS. Passwords and tokens travel unencrypted between the browser or the agent and the stack; there is no lockout after failed sign-ins. Safeguards against accidents are part of paper-boxing (path containment, overwrite and delete intent, token scopes); measures against strangers are not, and it is not for the public internet.

Links inside a site work when they are relative to the site; a link from the host root such as `/css/x.css` does not, because every site lives under `/<site>/`.

## Endpoints

Backend, port 35843: the REST API under `/api/v1` ([`docs/api.md`](docs/api.md)), `GET /health`, `GET /admin/version`, `GET /docs`.

Frontend, port 35840: the pages `/login`, `/` (sites), `/sites/<slug>`, `/tokens`, `/users`, `/account`; `GET /health`, `GET /admin/version`.

MCP server, port 35842: `POST /mcp` (Streamable HTTP; `GET` and `DELETE` answer 405, and the old `/sse` path answers 405 naming `/mcp`), `GET /health`, `GET /admin/version`, `GET /docs`.

Site server, port 35841: `GET /<site>/...`, the files as uploaded. A folder with no `index.html` shows nginx's listing.

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

Tool calls carry files up to `PAPER_BOXING_MCP_MAX_FILE_MB` (default 8 MiB). A larger file goes through the REST API with the same token: `PUT` and `GET /api/v1/sites/<site>/files/<path>`.

## Configuration

Every variable is read from the environment at startup. `HOST` defaults to `127.0.0.1` in code; the images set `0.0.0.0`.

Backend:

| Variable | Default | Purpose |
|---|---|---|
| `HOST` | `127.0.0.1` (image: `0.0.0.0`) | bind address |
| `PORT` | `35843` | listen port |
| `PAPER_BOXING_DATA_DIR` | `./data` (image: `/data`) | the volume: `sites/`, `staging/`, `paper-boxing.sqlite3` |
| `PAPER_BOXING_PUBLIC_SITES_URL` | `http://127.0.0.1:35841` | the site server's address as people and links reach it; every site's URL is built from it |
| `PAPER_BOXING_ADMIN_USERNAME` | unset | the first account, created only when no account exists; the same rules as any account (`[A-Za-z0-9][A-Za-z0-9._-]*`, at most 64 characters) |
| `PAPER_BOXING_ADMIN_PASSWORD` | unset | its password, at least 8 characters; ignored once an account exists. An invalid pair stops the backend from starting |
| `PAPER_BOXING_MAX_UPLOAD_MB` | `200` | size cap of one uploaded file |
| `PAPER_BOXING_SESSION_DAYS` | `14` | sliding expiry of a UI session |

Frontend:

| Variable | Default | Purpose |
|---|---|---|
| `HOST` | `127.0.0.1` (image: `0.0.0.0`) | bind address |
| `PORT` | `35840` | listen port |
| `PAPER_BOXING_BACKEND_URL` | `http://127.0.0.1:35843` | the backend (`http://backend:35843` in the stack) |
| `PAPER_BOXING_PUBLIC_SITES_URL` | `http://127.0.0.1:35841` | as for the backend |
| `PAPER_BOXING_STORAGE_SECRET` | unset, required | signs the per-user session cookie; the frontend refuses to start without it |

MCP server:

| Variable | Default | Purpose |
|---|---|---|
| `HOST` | `127.0.0.1` (image: `0.0.0.0`) | bind address |
| `PORT` | `35842` | listen port |
| `PAPER_BOXING_BACKEND_URL` | `http://127.0.0.1:35843` | the backend |
| `PAPER_BOXING_PUBLIC_SITES_URL` | `http://127.0.0.1:35841` | as for the backend |
| `PAPER_BOXING_MCP_MAX_FILE_MB` | `8` | largest file a tool call carries |
| `PAPER_BOXING_MCP_ENABLE_TRANSPORT_SECURITY` | `true` | Host and Origin validation (DNS-rebinding protection) on `/mcp` |
| `PAPER_BOXING_MCP_ALLOWED_HOSTS` | `localhost, 127.0.0.1, [::1]`, each also with `:*` | comma-separated `Host` values accepted on `/mcp`; a deployment adds `<host>:35842`. Another Host gets 421 |
| `PAPER_BOXING_MCP_ALLOWED_ORIGINS` | `http://localhost`, `http://127.0.0.1`, each also with `:35842` | browser origins accepted on `/mcp` and for CORS; a non-browser client sends no Origin and passes. Another Origin gets 403 |

There is no shared static token: an agent authenticates with an agent token that a signed-in user created, and the backend enforces the token's scope on every call, whichever client made it.

## Releasing

Tag-driven via the `Release` workflow on push of a `v*` tag. Use the [`ParkviewLab/dev-tools`](https://github.com/ParkviewLab/dev-tools) helpers; `pyproject.toml` is the only place the version lives, and the workflow's gate refuses a tag that does not match it.

```sh
git bump patch              # X.Y.Z → X.Y.(Z+1), committed
git release                 # annotated tag vX.Y.(Z+1) from pyproject.toml
git push --follow-tags      # CI fires
```

The workflow runs a **gate** (tag equals the version, tag reachable from `main`, version greater than the previous tag), then a **docker** matrix that builds and pushes the three images for amd64 and arm64 with the `X.Y.Z`, `X.Y` and `latest` tags, then a **changelog** job that writes the new [`CHANGELOG.md`](CHANGELOG.md) section (an LLM-written Highlights paragraph plus [`git-cliff`](https://git-cliff.org/)'s categorized list), commits it to `main`, and creates the GitHub Release. There is no PyPI publish.

### Commit message convention

PRs are squash-merged with the PR title as the commit subject, so the PR title carries the [Conventional Commit](https://www.conventionalcommits.org/) prefix that [`cliff.toml`](cliff.toml) reads: `feat:` and `fix:` and `perf:` are user-visible sections, `refactor:`, `docs:` and `test:` have their own, and `chore:`, `ci:`, `build:` and `style:` are dropped from the changelog but stay in history. See [`docs/CONTRIBUTING.md`](docs/CONTRIBUTING.md).

## License

Licensed under either of

- Apache License, Version 2.0 ([LICENSE-APACHE](LICENSE-APACHE) or <http://www.apache.org/licenses/LICENSE-2.0>), or
- MIT license ([LICENSE-MIT](LICENSE-MIT) or <http://opensource.org/licenses/MIT>)

at your option. In SPDX terms: `MIT OR Apache-2.0`.

Unless you explicitly state otherwise, any contribution intentionally submitted for inclusion in this work by you shall be dual-licensed as above, without any additional terms or conditions. See [LICENSING.md](LICENSING.md).

---
<sub>© 2026 Gary Frattarola · Licensed under [MIT](LICENSE-MIT) OR [Apache-2.0](LICENSE-APACHE) · part of [ParkviewLab](https://github.com/ParkviewLab)</sub>
