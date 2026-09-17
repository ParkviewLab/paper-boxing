<!--
SPDX-FileCopyrightText: 2026 Gary Frattarola <garyf@parkviewlab.ai>

SPDX-License-Identifier: MIT OR Apache-2.0
-->

# Deployment

How to build the paper-boxing stack and how to run it, with `docker compose` or as a Portainer stack. It is written for any reader of the public repository and names no private host or secret: `<host>` stands for the name or address at which your LAN reaches the Docker host, `<token>` for an agent token you created, `<project>` for the compose project or Portainer stack name. Where an example needs a concrete host name it uses `trixie.local`; substitute your own. Every command below was run against a stack built from this tree, with one exception: `docker compose pull` of the released images cannot run until the first release has published them.

The deployment model, stated plainly (the README says the same): one person's private LAN, plain HTTP, no TLS, no lockout after failed sign-ins. Passwords and tokens travel unencrypted between a browser or an agent and the stack. Do not expose the ports to the internet.

## The four services

| Service | Image | Port | Role |
|---|---|---|---|
| `backend` | `ghcr.io/parkviewlab/paper-boxing-backend` | 35843 | the REST API; the only writer of files and of the database |
| `frontend` | `ghcr.io/parkviewlab/paper-boxing-frontend` | 35840 | the web UI: the address people open |
| `mcp` | `ghcr.io/parkviewlab/paper-boxing-mcp` | 35842 | the MCP server for agents |
| `sites` | `nginx:stable-alpine` | 35841 | serves every site read-only; the address links point at, never to change |

The three paper-boxing images are built from one commit by the `Release` workflow, for `linux/amd64` and `linux/arm64`, and each carries the tags `X.Y.Z`, `X.Y` and `latest`. A deployment names one version for the whole stack; the frontend and the MCP server are clients of the backend's API and are only tested against the backend of the same version, so mixing versions is not supported. Each image listens inside the container on the same number it publishes (35843, 35840, 35842), so the port mappings are same-number; nginx listens on 80 inside and is published as 35841.

All state lives in one volume, mounted at `/data` in the backend and read-only in the site server: `sites/<slug>/...` is the served tree, `staging/` holds uploads in progress (on the same filesystem, so a finished upload moves into place by rename), and `paper-boxing.sqlite3` holds accounts, sessions, tokens, site metadata and an index of the files. The frontend and the MCP server hold no state of their own.

## Building the images

One `Dockerfile`, three targets. From a checkout:

```bash
docker build --target backend  -t paper-boxing-backend  .
docker build --target frontend -t paper-boxing-frontend .
docker build --target mcp      -t paper-boxing-mcp      .
```

The three share the dependency layers (the uv base image and the lock file), so after the first build the other two are mostly cache hits. The same builds run through compose with the override the integration tier uses, which tags the images `paper-boxing-<service>:local` so that a pulled image is never shadowed, and starts the stack from them with the tier's settings:

```bash
docker compose -f docker-compose.yml -f tests/integration/compose.build.yml \
  --env-file tests/integration/integration.env build
docker compose -f docker-compose.yml -f tests/integration/compose.build.yml \
  --env-file tests/integration/integration.env up -d --build --wait
```

`--wait` returns once every container with a health check is healthy. `PAPER_BOXING_INTEGRATION=1 uv run pytest -m integration -q` then runs the integration tier against that stack, and `down -v` with the same `-f` and `--env-file` arguments removes it, volume included. The values in `integration.env` are for that stack only.

## The compose file, service by service

[`docker-compose.yml`](../docker-compose.yml) is a copy-and-edit example. Every operator setting is a `${...}` variable read from a `.env` file beside the compose file, or from the stack's environment in Portainer, so the file itself is used unchanged. Two variables are secrets and have no default: compose refuses to start until they are set. [`.env.example`](../.env.example) lists every variable; the README's Configuration tables give every default.

| Variable | Read by | Secret | What it is |
|---|---|---|---|
| `PAPER_BOXING_PUBLIC_SITES_URL` | backend, frontend, mcp | no | the site server as people and links reach it, `http://<host>:35841`; every site's URL is built from it, so it is part of every link placed elsewhere and does not change afterwards. Unset, the file's placeholder `http://CHANGE-ME:35841` is used and the UI shows unusable links |
| `PAPER_BOXING_ADMIN_USERNAME` | backend | no | the first account, created at the first start only when no account exists (default `admin`; the same rules as any account: `[A-Za-z0-9][A-Za-z0-9._-]*`, at most 64 characters) |
| `PAPER_BOXING_ADMIN_PASSWORD` | backend | yes, required | its password, at least 8 characters. Ignored once any account exists, so it may be removed from the stack after the first sign-in |
| `PAPER_BOXING_MAX_UPLOAD_MB` | backend | no | the size cap of one uploaded file, in MiB (default 200) |
| `PAPER_BOXING_SESSION_DAYS` | backend | no | the sliding expiry of a UI session, in days (default 14) |
| `PAPER_BOXING_STORAGE_SECRET` | frontend | yes, required | a long random string that signs the per-browser session cookie; `openssl rand -hex 32` makes one. Changing it signs everyone out |
| `PAPER_BOXING_MCP_MAX_FILE_MB` | mcp | no | the largest file a tool call carries, in MiB (default 8); larger files go through the REST API |
| `PAPER_BOXING_MCP_ALLOWED_HOSTS` | mcp | no | the `Host` values agents connect with, comma-separated; `<host>:*` matches any port. A request with another Host gets 421 (DNS-rebinding protection). The file's default keeps the loopback names and adds a `CHANGE-ME:35842` placeholder |
| `PAPER_BOXING_MCP_ALLOWED_ORIGINS` | mcp | no | browser origins allowed on `/mcp` and for CORS; empty keeps the loopback defaults. Only a browser-based MCP client sends an Origin; Claude Code and other non-browser clients send none and pass |

Two settings are fixed in the file because they describe the stack itself: `PAPER_BOXING_DATA_DIR=/data` in the backend, and `PAPER_BOXING_BACKEND_URL=http://backend:35843` in the frontend and the MCP server, which reach the backend by its service name on the compose network. The backend's port is published as well, so that an agent can `PUT` a file larger than the MCP cap straight to the REST API with the same token; nothing else needs it from outside.

`backend` publishes 35843, mounts the data volume at `/data`, and has a health check on `/health`. `frontend` publishes 35840 and `mcp` 35842; both start once the backend is healthy (`depends_on` with `condition: service_healthy`) and have health checks of their own. Their start periods are 10 to 15 seconds, so `docker compose ps` shows them as `health: starting` for that long after a start. `sites` publishes 35841 and has no health check; `ps` shows it as `Up`.

The data volume is a named volume, `paper-boxing-data`, by default; Docker names it `<project>_paper-boxing-data`, where the project is the directory holding the compose file, or the stack's name in Portainer, and `docker volume ls` shows it. It survives `docker compose down`; only `down -v` deletes it. The bind-mount alternative is in the file as a comment: a host directory (`mkdir -p /srv/paper-boxing`) in place of the named volume in the backend's `volumes` and, read-only, in the site server's. Its files can be browsed and backed up from the host; they appear root-owned there, because the containers run as root.

### The nginx configuration

`sites` is a stock `nginx:stable-alpine` whose configuration is inline in the compose file, as a Compose `configs` entry with `content` mounted at `/etc/nginx/conf.d/default.conf`; so the stack is one file that can be pasted into Portainer's editor. Inline `content` needs Compose 2.23.1 or newer; if a Portainer rejects the file for that reason, write the block to a file on the host and bind-mount it at `/etc/nginx/conf.d/default.conf:ro` instead. The whole data volume is mounted read-only and `root` points at its `sites/` tree, so nothing beside that tree (the database, the staging area) is reachable over HTTP, and the mount works on every Docker version (a volume `subpath` would need Docker 26). The root of the site server itself, `http://<host>:35841/`, is the `sites/` folder, and `autoindex` lists the site folders there too.

What the directives do: `index index.html` serves a site's or a folder's `index.html` at its address; `autoindex on` (with `autoindex_exact_size off` and `autoindex_localtime on`) makes nginx list a folder that has none, which is what one wants of a site of a few hand-made pages before it has an index; `try_files $uri $uri/ =404` keeps a missing path a 404; `add_header Cache-Control "no-cache"` makes a replaced file show at once (the browser revalidates through the `ETag` on every request); and `types` adds `application/javascript` for `.mjs` and `application/manifest+json` for `.webmanifest` to the stock `mime.types`, which already covers `.html`, `.css`, `.js`, `.svg`, `.png`, `.woff2` and the rest. (In the compose file the `$` of `$uri` is written `$$`, which is how a literal dollar is spelt there.)

`absolute_redirect off` is there because nginx redirects `/<slug>` to `/<slug>/` (a folder's address needs the trailing slash), and by default it writes that redirect as an absolute URL from its own point of view: the host it was asked for and the port it listens on, which is 80 inside the container and is therefore omitted. A browser following `Location: http://<host>/<slug>/` lands on port 80 of the host, not on 35841, and the link breaks. With the directive off, the redirect is the path alone, `Location: /<slug>/`, and the browser keeps the host and port it used. The integration tier checks this on every run.

## Running with docker compose

```bash
cp .env.example .env    # then edit it: the sites URL, the admin password, the storage secret, the MCP hosts
docker compose up -d
docker compose ps
curl http://127.0.0.1:35843/health
```

A filled-in `.env`, with `trixie.local` standing for your Docker host:

```bash
PAPER_BOXING_PUBLIC_SITES_URL=http://trixie.local:35841
PAPER_BOXING_ADMIN_USERNAME=admin
PAPER_BOXING_ADMIN_PASSWORD=<a password of at least 8 characters>
PAPER_BOXING_STORAGE_SECRET=<the output of: openssl rand -hex 32>
PAPER_BOXING_MCP_ALLOWED_HOSTS=trixie.local:35842,localhost:*,127.0.0.1:*
```

`docker compose ps` lists the four containers; the backend is `healthy` within a few seconds, the frontend and the MCP server after their start periods, and `paper-boxing-sites` is `Up` (it has no health check). `/health` on 35843, 35840 and 35842 answers `{"ok": true, "version": "X.Y.Z", "uptime_seconds": ...}`, one version for the three. At the first start the backend's log records the first account, and every start records the settings it runs under:

```bash
docker compose logs backend
# paper-boxing-backend  | INFO: created the first account user=admin from the admin variables
# paper-boxing-backend  | INFO:     paper-boxing-backend v0.1.0 ready (data_dir=/data, public_sites_url=http://trixie.local:35841, max_upload_mb=200, session_days=14)
```

Open `http://<host>:35840/`, sign in with the admin pair, and create the accounts and tokens you need. To stop and start the stack without touching anything, `docker compose stop` and `docker compose start`. `docker compose down` removes the containers and the network and keeps the volume; `docker compose down -v` deletes the volume too, every site and account with it.

To upgrade, change the three image tags in the compose file to the new version (or keep `latest`), then `docker compose pull && docker compose up -d`. Compose re-creates the containers whose image changed; the volume and everything in it stay. The frontend keeps its per-browser sessions in the container, so after an update everyone signs in again; agent tokens live in the database and keep working.

## Running in Portainer

The compose file is written for Portainer's stacks: it uses no build step, no host paths unless you choose the bind mount, and reads every operator setting from the environment.

Create the stack, named for instance `paper-boxing`, in one of two ways. From the web editor, paste `docker-compose.yml` as it is, and pin a version by editing the three `image:` lines to the same `X.Y.Z` tag if you do not want `latest`. From the repository, give `https://github.com/ParkviewLab/paper-boxing` with the reference of a release tag (`refs/tags/vX.Y.Z`) and the compose path `docker-compose.yml`; the file at that tag names `latest`, so a stack from the repository follows the newest release at each re-deploy, which is what `latest` is for.

Enter the variables from `.env.example` as the stack's environment variables, one per name: `PAPER_BOXING_PUBLIC_SITES_URL`, `PAPER_BOXING_ADMIN_USERNAME`, `PAPER_BOXING_ADMIN_PASSWORD`, `PAPER_BOXING_STORAGE_SECRET` and `PAPER_BOXING_MCP_ALLOWED_HOSTS`, with the values shown above. The two secrets stay in Portainer's stack environment and appear nowhere in the repository; the two without a default in the compose file (the admin password and the storage secret) make the deployment fail with a clear message from compose if they are missing.

Deploy. The four containers appear under the stack with their fixed names (`paper-boxing-backend`, `-frontend`, `-mcp`, `-sites`) and health states; the backend goes healthy first, the frontend and the MCP server within their start periods. On the first start the backend creates `sites/` and `staging/` in the volume and the first account from the admin variables, and its log says so (`created the first account user=admin from the admin variables`). Open `http://<host>:35840/`: the sign-in page asks for the username and the password; sign in with the admin pair, and the top bar offers Sites, Tokens, Users and Account. Create the other accounts under Users, and change the admin password under Account if it was ever written anywhere. The admin variables are ignored from then on, so they may be removed from the stack's environment; a later re-deploy does not need them, because an account exists.

To update to a new version, change the three tags in the stack's editor (or keep `latest`), and update the stack with the option that pulls the images again; Portainer re-creates the containers, the volume stays, and everyone signs in to the UI again. Logs are each container's log view in Portainer, or `docker compose logs backend` on the host; the backend logs every authenticated call with the account and the token that made it (`route=upload_file user=admin token=tok_... type=agent via=mcp`), and never a password or a token secret. Health is the containers' health states and `GET /health` on 35840, 35842 and 35843, which return `{ok, version, uptime_seconds}`; the site server has no health endpoint, and any site's address answering is its check.

## Creating an agent token and connecting an MCP client

Sign in to the UI and open Tokens. Under "Create a token", give it a name (for example the agent and the machine it runs on), choose its scope, and press "Create token". The scopes are the handbook's tiers: `read_only` lists and downloads, `read_write` adds creating a site and uploading, `remove_destructive` adds every delete. A dialog shows the secret once, with a copy button; it is stored only as a hash, so copy it before pressing Done. The token belongs to the account that created it, does not expire, and lives until revoked on the same page, where every account's tokens are listed and any account may revoke any of them.

For Claude Code, the token goes in the `Authorization` header of a Streamable-HTTP server:

```bash
claude mcp add --transport http paper-boxing http://<host>:35842/mcp --header "Authorization: Bearer <token>"
claude mcp list        # paper-boxing: http://<host>:35842/mcp (HTTP) - ✔ Connected
claude mcp get paper-boxing
claude mcp remove paper-boxing
```

`claude mcp add` stores the server in the local configuration of the current project, which `claude mcp get` reports; `claude mcp list` connects to it and shows whether it is reachable. The agent then sees the tools its scope allows, three for `read_only`, eight for `remove_destructive`.

Any Streamable-HTTP MCP client connects the same way: the endpoint is `http://<host>:35842/mcp`, the method is `POST`, and every request carries `Authorization: Bearer <token>`, the `initialize` handshake included. This is the handshake with curl; the answer is a server-sent event carrying the JSON-RPC result:

```bash
curl -s -X POST http://<host>:35842/mcp \
  -H "Authorization: Bearer <token>" \
  -H "Content-Type: application/json" \
  -H "Accept: application/json, text/event-stream" \
  -d '{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2025-06-18","capabilities":{},"clientInfo":{"name":"curl","version":"0"}}}'
# event: message
# data: {"jsonrpc":"2.0","id":1,"result":{"protocolVersion":"2025-06-18","capabilities":{"experimental":{},"tools":{"listChanged":false}},"serverInfo":{"name":"paper-boxing-mcp","version":"0.1.0"}}}
```

The MCP server confirms the token with the backend on every request and accepts agent tokens only: a UI session token is refused with `403 wrong_token_type`, an unknown or revoked one with `401 unauthorized`. A tool call carries a file of up to `PAPER_BOXING_MCP_MAX_FILE_MB`; for a larger file the tool's error message points at the REST route, `PUT` or `GET /api/v1/sites/<site>/files/<path>` on `http://<host>:35843`, which the same agent token opens.

## Backing up and restoring `/data`

The volume holds three things, and they are backed up differently. `sites/` is plain files: archive it as it is. `staging/` holds uploads in progress and is emptied at every start: skip it. `paper-boxing.sqlite3` is a SQLite database in WAL mode, and while the backend runs its recent writes sit in `paper-boxing.sqlite3-wal` beside it; copying the file alone, or the pair mid-write, gives a copy that may be inconsistent or that misses the newest tokens and sites. SQLite's own backup command writes a consistent snapshot of the database while it is in use, and that is the copy to keep.

The backend image has no `sqlite3` binary, so run the backup from a throwaway Alpine container that mounts the volume and a host directory; the stack keeps running meanwhile:

```bash
docker run --rm -v <project>_paper-boxing-data:/data -v "$PWD":/backup alpine:3 sh -c \
  'apk add --no-cache sqlite >/dev/null && sqlite3 /data/paper-boxing.sqlite3 ".backup /backup/paper-boxing.sqlite3" && tar -C /data -czf /backup/sites.tgz sites'
```

The result is `paper-boxing.sqlite3` and `sites.tgz` in the current directory. With the host's `sqlite3`, `sqlite3 paper-boxing.sqlite3 'pragma integrity_check; select slug from sites;'` confirms the copy is a whole database and names the sites it knows. With the bind-mount alternative, the same two commands run on the host directory directly, `sqlite3` permitting; the point stands either way: the database is copied through `.backup`, never as a file while the backend runs.

To restore, on the same host or a new one: stop the backend (stop the stack, or `docker compose stop`), put the two things back, and start it again. Remove the old database with its `-wal` and `-shm` files before copying the backup in, so that a stale write-ahead log is not applied to the restored file:

```bash
docker compose stop
docker run --rm -v <project>_paper-boxing-data:/data -v "$PWD":/backup alpine:3 sh -c \
  'rm -rf /data/sites /data/paper-boxing.sqlite3 /data/paper-boxing.sqlite3-wal /data/paper-boxing.sqlite3-shm && tar -C /data -xzf /backup/sites.tgz && cp /backup/paper-boxing.sqlite3 /data/paper-boxing.sqlite3'
docker compose start
```

On a new host, deploy the stack first so that the volume exists, then restore into it; the account in the restored database replaces the one the first start created, so sign in with the backed-up password, not the one in the new stack's environment. After the restore every site is served again at its old address, and the API lists it.

What the `files` table in the database means for a restore: it is an index of the served tree (size, modification time and sha256 per file), kept by every upload and deletion and used for the file counts and sizes the UI shows, and it is reconciled against the disk whenever a folder is listed. A file whose size or modification time no longer matches its row (an archive restores modification times to the second, so this is the normal case after a restore) is hashed again and its row rewritten; a row whose file is gone is dropped; a file that is on disk without a row is indexed. So the database and the `sites/` archive need not be from the same instant: after a restore the index catches up folder by folder as sites are opened, and a site's counts may be a little stale until then. The one thing that must be restored together is the pair itself: a site folder under `sites/` without its row in the database is not a site, the API does not list it, and a new site with that slug is refused (`409 site_exists`) until the folder is removed; a row without its folder is a site with no files.

## Troubleshooting

- A `421` from the MCP server, code `forbidden`, message `the Host '<host>:35842' is not in PAPER_BOXING_MCP_ALLOWED_HOSTS`: the host name or address the client used is missing from the allowlist. Add `<host>:35842` (or `<host>:*`) to `PAPER_BOXING_MCP_ALLOWED_HOSTS` and update the stack; the MCP server restarts with the new list.
- A `403` from the MCP server with code `forbidden` and a message naming `PAPER_BOXING_MCP_ALLOWED_ORIGINS`: a browser-based client sent an `Origin` outside the allowlist. Add the origin. A non-browser client never meets this.
- A `403` with code `wrong_token_type`: the token is a UI session token; the MCP server takes agent tokens only. Create one under Tokens.
- A `401` with code `unauthorized` (and a `WWW-Authenticate: Bearer` header): no token was sent, or the token is unknown or revoked. The message says which.
- A `503` with code `backend_unreachable` from the MCP server: the backend is down or unreachable on the compose network; the MCP server itself is up, and `/health` on 35842 says so. Check `docker compose ps` and `docker compose logs backend`. Once the backend answers again the MCP server recovers on its own, without a restart.
- A link to a site loses the port, `http://<host>/<slug>/` instead of `http://<host>:35841/<slug>/`: nginx wrote the redirect from `/<slug>` to `/<slug>/` as an absolute URL, because `absolute_redirect off` is missing from its configuration (see above). With the directive, `curl -i http://<host>:35841/<slug>` answers `301` with `Location: /<slug>/`.
- Site URLs shown by the UI point at `CHANGE-ME` or at the wrong host: `PAPER_BOXING_PUBLIC_SITES_URL` is unset or wrong. It must be the address people use, not the container's.
- A `507` with code `insufficient_storage` from the backend: the data volume is full or over quota. Nothing was half-written, and the old file, where there was one, is whole; free space and upload again.
- Portainer rejects the compose file at `configs`: its Compose is older than 2.23.1. Write the nginx block to a file on the host and bind-mount it at `/etc/nginx/conf.d/default.conf:ro`, as described under the nginx configuration.
- The stack does not start and compose says `required variable PAPER_BOXING_ADMIN_PASSWORD is missing a value` (or `PAPER_BOXING_STORAGE_SECRET`): the variable is unset in `.env` or in the stack's environment; both are required and have no default.
- The frontend exits at start with a message about `PAPER_BOXING_STORAGE_SECRET`: it was started outside compose without the variable. Set it.
- The backend exits at start with a message about `PAPER_BOXING_ADMIN_USERNAME` or `PAPER_BOXING_ADMIN_PASSWORD`: no account exists yet and the pair does not meet the rules (the username `[A-Za-z0-9][A-Za-z0-9._-]*` of at most 64 characters, the password at least 8). Fix the variables and update the stack; once an account exists the pair is ignored.
- Everyone is asked to sign in again after an update: expected. The frontend keeps its per-browser sessions in the container, not in the volume. Agent tokens are unaffected.

---
<sub>© 2026 Gary Frattarola · Licensed under [MIT](../LICENSE-MIT) OR [Apache-2.0](../LICENSE-APACHE) · part of [ParkviewLab](https://github.com/ParkviewLab)</sub>
