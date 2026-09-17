<!--
SPDX-FileCopyrightText: 2026 Gary Frattarola <garyf@parkviewlab.ai>

SPDX-License-Identifier: MIT OR Apache-2.0
-->

# The API contract

This is the contract between the backend and its two clients, the frontend and the MCP server. It is fixed by the scaffold so that the three components can be built in parallel (`design.md`, section 9). The machine-readable form lives in `src/paper_boxing/common/`: `schema.py` (every request and response body), `routes.py` (the route table the backend, the fake backend and the client all use), `naming.py` (slug and path rules), `scopes.py` (token types and tiers) and `client.py` (the REST client). `common/fake_backend.py` is an in-memory implementation of everything below; `tests/contract/` exercises it route by route, and the backend worker runs the same suite against the real app. A test checks that every route in the table is named in this document.

## Conventions

Every route lives under `/api/v1` and speaks JSON, except file bodies. Every route except `POST /api/v1/auth/login` needs `Authorization: Bearer <token>`, where the token is either a session token (issued at sign-in, for the UI) or an agent token (created by a user, for agents). `/health`, `/admin/version` and `/docs` sit outside `/api/v1` and need no token.

A request that came through the MCP server carries `X-Paper-Boxing-Via: mcp`; the backend logs it as such, under the token that acted. Nothing else changes.

Timestamps are ISO 8601 with a UTC offset. Sizes are bytes. Digests are lowercase hex sha256.

### Errors

Every error response has the body `{"error": {"code": "<code>", "message": "<text>"}}`, whatever produced it (a route, the framework's 404 and 405, request validation, an unexpected exception). The codes, with their statuses:

| Status | Codes |
|---|---|
| 400 | `invalid_name` (no slug can be derived), `invalid_path`, `confirm_mismatch`, `bad_request` |
| 401 | `unauthorized` (missing, unknown, revoked or expired token), `invalid_credentials` (sign-in) |
| 403 | `forbidden` (the token's scope is too low), `session_required` (an agent token on a session-only route), `wrong_password`; `wrong_token_type` is the MCP server's own, for a session token presented to it |
| 404 | `not_found` |
| 405 | `method_not_allowed` |
| 409 | `site_exists`, `file_exists`, `folder_not_empty`, `not_a_file`, `not_a_folder`, `user_exists`, `last_user` |
| 413 | `payload_too_large` |
| 422 | `validation_error` (a body or parameter failed validation) |
| 500 | `internal_error` |
| 503 | `backend_unreachable` is the MCP server's own: the backend could not be reached, or did not confirm a token as this contract says |
| 507 | `insufficient_storage` (disk full or quota; nothing half-written) |

The MCP server answers its own HTTP errors in the same shape: `401 unauthorized` for a token the backend does not confirm, `403 wrong_token_type` for a session token, and `503 backend_unreachable` when the backend cannot be reached or does not answer the confirmation as this contract says (`design.md`, section 4a).

### Access

Each route has one access rule. `public`: no token. `any_token`: any valid token, whatever its scope. `read_only`, `read_write`, `remove_destructive`: a token of at least that scope, where a session token acts with the full rights of its user (`remove_destructive`) and an agent token with the scope it was created with. `session`: a session token only, because managing accounts and tokens is a person's act; an agent token gets `403 session_required`. The tiers are ordered `read_only` (list and download) < `read_write` (adds create site and upload) < `remove_destructive` (adds every delete). A missing or invalid token is `401 unauthorized` on every non-public route, before any other check.

### Names and paths

A site's slug is derived from its display name: accents stripped, lowercased, every run of characters other than `a-z0-9` becomes one hyphen, leading and trailing hyphens go, and the result is cut to 63 characters. A name from which nothing usable remains is `400 invalid_name`; a slug already taken is `409 site_exists`. The slug never changes afterwards.

A path inside a site is relative and `/`-separated; the canonical form has no leading, trailing or doubled slashes. Leading and trailing slashes are stripped; an empty segment (a doubled slash) is `400 invalid_path`. Rejected as `400 invalid_path`: `.` and `..` segments, backslashes, control characters, a segment over 255 bytes, a path over 1024 characters, and (for a file operation) the empty path. A path containing a newline cannot be routed at all and answers 404 rather than 400, on both implementations, because the framework's path parameter stops at a newline. The backend additionally resolves every path on disk and refuses one that escapes the site, symlinks included. Folders come into being with the first file below them and stay until deleted; there is no route that creates an empty folder, since an empty folder has no meaning in a static site.

In a URL, `{path}` is a multi-segment parameter: its slashes are literal separators, every other reserved or non-ASCII character is percent-encoded, and the server validates the decoded path, so `%2F` arrives as a separator and cannot name a file containing a slash. Responses carry the decoded canonical path.

## Routes

| Method and path | Access | Behaviour |
|---|---|---|
| `POST /api/v1/auth/login` | public | Body `LoginRequest {username, password}`. 200 `LoginResponse {token, expires_at, user}`; 401 `invalid_credentials`. |
| `POST /api/v1/auth/logout` | session | Ends the calling session. 204. |
| `GET /api/v1/sites` | read_only | 200 `SiteList {sites: [Site]}`, sorted by slug. |
| `POST /api/v1/sites` | read_write | Body `CreateSiteRequest {name}`. 201 `Site`; 400 `invalid_name`; 409 `site_exists`. |
| `GET /api/v1/sites/{slug}` | read_only | 200 `Site`; 404. |
| `DELETE /api/v1/sites/{slug}?confirm=` | remove_destructive | Deletes the site and every file in it. 204; 400 `confirm_mismatch` unless `confirm` equals the slug; 404. An unknown slug is 404 before a wrong `confirm` is 400. |
| `GET /api/v1/sites/{slug}/files?path=` | read_only | Lists one folder (`path` empty or absent is the root). 200 `FileListing {site, path, entries: [FileEntry]}`, folders first then files, each by name; for a folder entry `bytes` is the recursive total, `modified_at` the newest time below it and `sha256` null; 404 for a missing site or folder; 409 `not_a_folder` when `path` is a file. |
| `PUT /api/v1/sites/{slug}/files/{path}?overwrite=false` | read_write | The raw request body is the file; missing parent folders are created. 201 `UploadResult {path, bytes, sha256, replaced: false}` for a new file, 200 with `replaced: true` when `overwrite=true` replaced one; 409 `file_exists` for an existing file without `overwrite=true`; 409 `not_a_file` when a folder is at the path; 409 `not_a_folder` when a parent segment is a file; 413 `payload_too_large` above the cap; 400 `invalid_path`; 404 for a missing site. The checks run in this order: the site (404), the path (400), the size cap (413), then the conflicts (409). `overwrite=true` where no file exists succeeds as 201 with `replaced: false`; a zero-byte body is a valid upload. A replacement is atomic: a failure leaves the old file whole. |
| `GET /api/v1/sites/{slug}/files/{path}` | read_only | 200 with the bytes, `Content-Type` guessed from the extension (`application/octet-stream` otherwise) and `Content-Disposition: attachment; filename=...`; 404; 409 `not_a_file` for a folder. The backend may answer 206 to a `Range` request, whereas the fake answers 200 with the whole body. |
| `DELETE /api/v1/sites/{slug}/files/{path}` | remove_destructive | 204; 404; 409 `not_a_file` for a folder. |
| `DELETE /api/v1/sites/{slug}/folders/{path}?recursive=false` | remove_destructive | 204; 409 `folder_not_empty` for a non-empty folder without `recursive=true`; 404; 409 `not_a_folder` for a file; 400 `invalid_path` for the site root (delete the site instead). |
| `GET /api/v1/tokens` | session | 200 `TokenList {tokens: [Token]}`: every live agent token of every user, with its owner's `username`, oldest first. Sessions are not listed. |
| `POST /api/v1/tokens` | session | Body `CreateTokenRequest {name, scope}`. 201 `TokenCreated {token: Token, secret}`; the secret is returned here and never again. The token belongs to the calling user. |
| `GET /api/v1/tokens/self` | any_token | 200 `TokenSelf {id, name, type, scope, username, expires_at}` for the calling token, `expires_at` being null for an agent token and set for a session; 401 for an unknown, revoked or expired one. The MCP server calls this on every request. |
| `DELETE /api/v1/tokens/{token_id}` | session | Revokes an agent token. 204; 404 for an unknown, already revoked, or session token. Any user may revoke any agent token: accounts have equal rights. |
| `GET /api/v1/users` | session | 200 `UserList {users: [User]}`, by username. |
| `POST /api/v1/users` | session | Body `CreateUserRequest {username, password}` (username `[A-Za-z0-9][A-Za-z0-9._-]*`, at most 64; password at least 8 characters). 201 `User`; 409 `user_exists`; 422. |
| `POST /api/v1/users/self/password` | session | Body `ChangePasswordRequest {current, new}`. 204; 403 `wrong_password`. Signs out the caller's other sessions; the calling session and agent tokens are untouched. |
| `DELETE /api/v1/users/{user_id}` | session | 204; 409 `last_user` for the last account; 404. Every token of the removed account is revoked. A user may remove their own account when it is not the last. |

File operations are single-file only (`design.md`, section 4): there is no batch or folder upload. A client that wants a site to match a new build lists, uploads what changed and deletes what is gone.

### Tokens and sessions

A session token is issued by `login` and expires after a sliding period (`PAPER_BOXING_SESSION_DAYS`, default 14): every authenticated request moves the expiry forward. An agent token does not expire; it lives until revoked, or until its owner's account is removed, and its `expires_at` is null wherever it is shown, whereas a session's is set. A password change signs out the user's other sessions. Both kinds are random 32-byte secrets, presented as `pb_` followed by 43 URL-safe base64 characters, stored only as sha256 hashes, and compared in constant time. Every token records when it was last used.

## Ops endpoints

`GET /health` returns `{ok, version, uptime_seconds}` and `GET /admin/version` returns the service's `name`, `version` and its configured paths or URLs, on all three services. The backend and the MCP server also serve `GET /docs`.

## MCP tools

The MCP server exposes eight tools, each with a title, annotations, an `inputSchema` and an `outputSchema` generated from the models in `src/paper_boxing/mcp/schema.py`, and the specifications in `src/paper_boxing/mcp/tools.py`. A tool is listed to, and may be called by, a token whose scope reaches the tool's tier; a call from a lower scope is refused. Every failure is reported as a tool error, never as an exception the client cannot read.

| Tool | Scope | Input | Output |
|---|---|---|---|
| `list_sites` | read_only | none | `{sites: [Site]}` |
| `create_site` | read_write | `{name}` | `Site` |
| `list_files` | read_only | `{site, path=""}` | `FileListing` |
| `upload_file` | read_write | `{site, path, content_base64, overwrite=false}` | `UploadResult` |
| `download_file` | read_only | `{site, path}` | `{site, path, bytes, sha256, content_type, content_base64}` |
| `delete_file` | remove_destructive | `{site, path}` | `{site, path, deleted: true}` |
| `delete_folder` | remove_destructive | `{site, path, recursive=false}` | `{site, path, deleted: true}` |
| `delete_site` | remove_destructive | `{site, confirm}` | `{slug, deleted: true}` |

`upload_file` and `download_file` carry files up to `PAPER_BOXING_MCP_MAX_FILE_MB` (default 8 MiB, decoded); their descriptions tell the agent that a larger file goes through `PUT` and `GET /api/v1/sites/{site}/files/{path}` with the same token.

The MCP server's transport rules:

- `/mcp` accepts `POST` only; `GET` and `DELETE` answer 405.
- `/sse` answers 405 on every method, with a body naming `/mcp`.
- A request whose `Host` is outside `PAPER_BOXING_MCP_ALLOWED_HOSTS` gets 421.
- A request whose `Origin` is outside `PAPER_BOXING_MCP_ALLOWED_ORIGINS` gets 403; a request with no Origin passes.
- CORS is limited to the same origin allowlist.

Before any tool runs, and on every request, the MCP server confirms the request's bearer token with `GET /api/v1/tokens/self`, keeps nothing between requests, and accepts only tokens of type `agent`; it then forwards the token to the backend with `X-Paper-Boxing-Via: mcp`, so the backend enforces the scope again and logs the call under that token. Inside a tool handler the low-level SDK exposes the HTTP request as `mcp.request_context.request`; the bearer is its `authorization` header.

## Notes for the three workers

- The frontend and the MCP server hold one `httpx.AsyncClient` each and wrap it in `BackendClient`; every call passes the current user's or agent's token explicitly. For tests, build the httpx client with `httpx.ASGITransport(app=create_fake_backend(...))`.
- The fake backend's state is `app.state.fake` (`FakeState`): `add_user`, `issue_session`, `issue_agent_token`, `revoke`, `clock.advance(seconds)` for expiry, and `requests` (every call with its route name, token id and Via header) for proving that a token was confirmed on each request. `tests/contract/conftest.py` shows the idiom.
- NiceGUI's pytest fixtures: load `nicegui.testing.user_plugin` (not `nicegui.testing.plugin`, which imports selenium).
- The backend's routes register the templates from `routes.py`, and `tests/contract/` is parametrised so that the real app can be added beside the fake; when both pass, the contract holds.
