<!--
SPDX-FileCopyrightText: 2026 Gary Frattarola <garyf@parkviewlab.ai>

SPDX-License-Identifier: MIT OR Apache-2.0
-->

# Deployment

How to build the paper-boxing stack and how to run it, with `docker compose` or as a Portainer stack. This is written for any reader of the public repository; it names no private host or secret. Replace `<host>` with the name or address at which your LAN reaches the Docker host.

This document is a draft from the scaffold. The integration pull request completes it from instructions its job has actually run, and the backend, frontend and MCP pull requests fill in the parts marked as theirs.

## The four services

| Service | Image | Port | Role |
|---|---|---|---|
| `backend` | `ghcr.io/parkviewlab/paper-boxing-backend` | 35843 | the REST API; the only writer of files and of the database |
| `frontend` | `ghcr.io/parkviewlab/paper-boxing-frontend` | 35840 | the web UI: the address people open |
| `mcp` | `ghcr.io/parkviewlab/paper-boxing-mcp` | 35842 | the MCP server for agents |
| `sites` | `nginx:stable-alpine` | 35841 | serves every site read-only; the address links point at, never to change |

Each paper-boxing image carries the tags `X.Y.Z`, `X.Y` and `latest`, for `linux/amd64` and `linux/arm64`, and the three are built from one commit with one version. A deployment names one version for the whole stack; mixing versions is not supported. The images listen on their own port numbers inside the container too, so the port mappings are same-number.

All state lives in one volume mounted at `/data` in the backend: `sites/<slug>/...` is the served tree, `staging/` holds uploads in progress, and `paper-boxing.sqlite3` holds accounts, sessions, tokens and site metadata. The site server mounts the same volume read-only.

## Building the images locally

From a checkout, one target per image:

```bash
docker build --target backend  -t paper-boxing-backend  .
docker build --target frontend -t paper-boxing-frontend .
docker build --target mcp      -t paper-boxing-mcp      .
```

Or build and start the whole stack from the tree with the integration override, which tags the images locally so a pulled image is never shadowed:

```bash
docker compose -f docker-compose.yml -f tests/integration/compose.build.yml \
  --env-file tests/integration/integration.env up -d --build --wait
```

## The compose file, service by service

[`docker-compose.yml`](../docker-compose.yml) is a copy-and-edit example. Every setting is a `${...}` variable with a default, read from a `.env` file beside it or from Portainer's stack environment; the two secrets have no default, and compose refuses to start until they are set. [`.env.example`](../.env.example) lists them all.

`backend` publishes 35843 and mounts the data volume at `/data`. Its variables: `PAPER_BOXING_PUBLIC_SITES_URL` (the site server's address as people and links reach it, `http://<host>:35841`; every site's URL is built from it), `PAPER_BOXING_ADMIN_USERNAME` and `PAPER_BOXING_ADMIN_PASSWORD` (the first account, created only when no account exists; ignored afterwards; the password is a secret), `PAPER_BOXING_MAX_UPLOAD_MB` (the size cap of one file, default 200) and `PAPER_BOXING_SESSION_DAYS` (the sliding expiry of a UI session, default 14).

`frontend` publishes 35840 and talks to the backend as `http://backend:35843` on the compose network. Its secret is `PAPER_BOXING_STORAGE_SECRET`, a long random string (`openssl rand -hex 32`) that signs the per-user session cookie; changing it signs everyone out.

`mcp` publishes 35842 and talks to the backend the same way. `PAPER_BOXING_MCP_ALLOWED_HOSTS` is the list of `Host` values agents connect with, `<host>:35842` for a LAN deployment (DNS-rebinding protection: a request with another Host gets 421). `PAPER_BOXING_MCP_ALLOWED_ORIGINS` is for browser-based clients only; a non-browser MCP client sends no Origin and passes. `PAPER_BOXING_MCP_MAX_FILE_MB` (default 8) caps the file a tool call carries; larger files go through the REST API.

`sites` is a stock nginx with its configuration inline in the compose file, mounting the data volume read-only with `root` at its `sites/` tree: a site's `index.html` is served at `/<slug>/`, a folder without one shows nginx's listing, a missing path is a 404, `absolute_redirect off` keeps the published port in the redirect from `/<slug>` to `/<slug>/`, `Cache-Control: no-cache` makes a replaced file show at once (still revalidated through ETag), and `.mjs` and `.webmanifest` have their types. Inline `configs` need Compose 2.23.1 or newer; if a Portainer rejects the file for that reason, write the block to a file on the host and bind-mount it at `/etc/nginx/conf.d/default.conf:ro` instead.

The data volume is a named volume by default. The bind-mount alternative (a host directory you can browse, root-owned since the containers run as root) is commented in the file; use the same path for the backend's `/data` and the site server's read-only `/data`.

## Running with docker compose

```bash
cp .env.example .env         # set PAPER_BOXING_PUBLIC_SITES_URL, the admin password, the storage secret, the MCP hosts
docker compose up -d
docker compose ps            # every service healthy
curl http://127.0.0.1:35843/health
```

To upgrade to a new version, change the tags in the compose file (or keep `latest`), then `docker compose pull && docker compose up -d`. `docker compose down` stops the stack and keeps the volume; `docker compose down -v` also deletes every site and account.

## Running in Portainer

Create the stack from the web editor (paste `docker-compose.yml`) or from the repository (the compose path is `docker-compose.yml`). Enter the variables from `.env.example` as the stack's environment variables: the public sites URL, the admin username and password, the storage secret, and the MCP allowed hosts. Deploy the stack; the four containers appear with fixed names (`paper-boxing-backend`, `-frontend`, `-mcp`, `-sites`) and health states.

First start: the backend creates `sites/` and `staging/` in the volume and the first account from the admin variables. Open `http://<host>:35840/` and sign in with them; then create the accounts and agent tokens you need. The admin variables are ignored from then on, so they can be removed from the stack afterwards. (The sign-in flow belongs to the frontend pull request; this paragraph is completed there.)

Updating: change the image tags in the stack's editor (or keep `latest` and re-pull), then update the stack with "re-pull image". Logs and health: each container's log view in Portainer, and `GET /health` on 35840, 35842 and 35843, which return `{ok, version, uptime_seconds}`.

## Creating an agent token and connecting an MCP client

Sign in to the UI, open Tokens, create a token with the scope the agent needs (`read_only`, `read_write` or `remove_destructive`), and copy it: it is shown once. Then, for Claude Code:

```bash
claude mcp add --transport http paper-boxing http://<host>:35842/mcp --header "Authorization: Bearer <token>"
```

Any Streamable-HTTP MCP client works the same way: the endpoint is `http://<host>:35842/mcp`, the header is `Authorization: Bearer <token>`. A UI session token is refused; only agent tokens are accepted.

## Backing up and restoring `/data`

Sites are plain files under `sites/`; copy them as they are. The SQLite database must not be copied while it is in use; take a consistent snapshot with SQLite's own backup, from a container that has the tool or from the host with the bind-mount alternative:

```bash
docker run --rm -v paper-boxing_paper-boxing-data:/data -v "$PWD":/backup alpine:3 sh -c \
  'apk add --no-cache sqlite >/dev/null && sqlite3 /data/paper-boxing.sqlite3 ".backup /backup/paper-boxing.sqlite3" && tar -C /data -czf /backup/sites.tgz sites'
```

The volume's name is the compose project's name plus `_paper-boxing-data`; `docker volume ls` shows it. To restore, stop the stack, put `paper-boxing.sqlite3` and `sites/` back into the volume the same way, and start it again.

## Troubleshooting

- A 421 from the MCP server: the `Host` the client used is not in `PAPER_BOXING_MCP_ALLOWED_HOSTS`; add `<host>:35842` (or `<host>:*`) and update the stack.
- A 403 from the MCP server on a browser-based client: the Origin is not in `PAPER_BOXING_MCP_ALLOWED_ORIGINS`.
- A 401 from the MCP server: the token is unknown, revoked or expired; a 403 with `wrong_token_type`: a UI session token was used where an agent token is needed.
- A link to a site that loses the port (`http://<host>/<slug>/` instead of `http://<host>:35841/<slug>/`): `absolute_redirect off` is missing from the nginx configuration.
- Site URLs shown by the UI point at the wrong host: `PAPER_BOXING_PUBLIC_SITES_URL` is wrong; it must be the address people use, not the container's.
- Portainer rejects the compose file at `configs`: its Compose is older than 2.23.1; use the bind-mounted nginx configuration described above.
- The frontend exits at start with a message about `PAPER_BOXING_STORAGE_SECRET`: set the variable.
