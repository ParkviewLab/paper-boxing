<!--
SPDX-FileCopyrightText: 2026 Gary Frattarola <garyf@parkviewlab.ai>

SPDX-License-Identifier: MIT OR Apache-2.0
-->

# Contributing

> The authoritative, org-wide version of these conventions is the [ParkviewLab handbook](https://github.com/ParkviewLab/handbook/tree/main).

This repo follows the ParkviewLab conventions. The essentials:

## Branch & PR flow

- Branch off **`develop`** into an ephemeral worktree named with a prefix: `feature-`, `bug-`/`fix-`, `doc-`, `test-`, `ops-`, `ci-`, `build-`, `release-` (hyphen, not slash). See the handbook's [`branching.md`](https://github.com/ParkviewLab/handbook/blob/main/docs/branching.md).
- Open a PR into **`develop`**. The repo is **merge-commit only**, so the merge button can only make a merge commit; **merging is the maintainer's action**, except the release's back-merge pull request, which `git back-merge` merges under the release's own authorisation. See the handbook's [`branching.md`](https://github.com/ParkviewLab/handbook/blob/main/docs/branching.md#who-merges).
- The promotion to `main` is made from the command line, not through a pull request; see [Releasing](#releasing) below.

## Commit / PR-title convention (this is what the changelog reads)

Because a PR is merged with a merge commit titled `<PR title> (#N)`, **the PR title becomes the commit subject**, and the changelog is generated from it (by dev-tools' shared `generate-changelog`, which lists every merged PR by its title, under the section its type names). Prefix every PR title with a [Conventional Commit](https://www.conventionalcommits.org/) type; see the handbook's [`commits-and-changelogs.md`](https://github.com/ParkviewLab/handbook/blob/main/docs/commits-and-changelogs.md#conventional-commit-prefixes) for the groups a title's type is sorted into and what an unrecognised or missing type gets.

## Local checks before opening a PR

Run the same checks CI requires, so the PR is green on arrival:

```bash
uv sync
uv run ruff check src tests
uv run ruff format --check src tests
uv run ty check
uv run pytest -m "not network and not integration" -q
uvx --from "reuse[charset-normalizer]" reuse lint
```

A PR **can't be merged until the required checks pass**: the workflows in [`.github/workflows/`](../.github/workflows/). Push after each commit. See also the handbook's [`python-tooling.md`](https://github.com/ParkviewLab/handbook/blob/main/docs/python-tooling.md) and [`testing.md`](https://github.com/ParkviewLab/handbook/blob/main/docs/testing.md).

## Testing

There are two tiers, and what each covers is in [`architecture.md`](architecture.md#tests). The unit and component tier is the `pytest` line above; the contract suite within it runs alone with `uv run pytest tests/contract -q`. The integration tier runs against the compose stack built from the tree and drives the `docker` CLI as well (it inspects the volume through the backend container and stops and starts the backend once); CI runs it on every pull request, and locally it is:

```bash
docker compose -f docker-compose.yml -f tests/integration/compose.build.yml --env-file tests/integration/integration.env up -d --build --wait
PAPER_BOXING_INTEGRATION=1 uv run pytest -m integration -q
docker compose -f docker-compose.yml -f tests/integration/compose.build.yml --env-file tests/integration/integration.env down -v
```

`PAPER_BOXING_INTEGRATION_HOST` (default `127.0.0.1`) is the host at which the tier reaches the published ports.

The fake backend is the idiom behind the frontend's and the MCP server's tests. `paper_boxing.common.fake_backend.create_fake_backend()` is an in-memory implementation of the whole contract, a faithful model rather than a stub. The frontend and the MCP server each hold one `httpx.AsyncClient` wrapped in `BackendClient`, and every call passes the caller's token explicitly; a test points that client at the fake by building it with `httpx.ASGITransport(app=create_fake_backend(...))`, through the one seam each component exposes for it: `server.http_client_factory` for the MCP server, the `http_client_factory` argument of `install()` for the frontend. The fake's state is `app.state.fake` (`FakeState`): `add_user`, `issue_session`, `issue_agent_token`, `revoke`, `clock.advance(seconds)` for expiry, and `requests` (every call with its route name, token id and Via header), which is how a test proves that a token was confirmed on each request. `tests/contract/conftest.py` shows the idiom, and `tests/conftest.py` the MCP fixtures: one session-scoped `TestClient`, because the session manager refuses to start twice, with the environment set before the server module is imported.

In the contract suite, the `real` leg is the backend proper over a temporary data directory with the cheap argon2 profile from `tests/_backend_helpers.py`; it assumes a case-sensitive filesystem, as CI's Linux is. On macOS's default case-insensitive filesystem two paths that differ only by case name the same file; that is a limitation of local development, not a defect.

The frontend's tests use NiceGUI's pytest fixtures: load `nicegui.testing.user_plugin` (not `nicegui.testing.plugin`, which imports selenium). The `user` fixture executes `tests/frontend_main.py` afresh per test (`main_file` in `pyproject.toml`), which installs the frontend against a fake backend; `tests/frontend_fixtures.py` adds `frontend_state`, the fake behind the test, and `second_user`, another simulated browser with its own cookie jar, for the multi-session tests.

## The contract between the components

`docs/api.md` and `src/paper_boxing/common/` fix the REST API and the MCP tools that the backend, the frontend and the MCP server share. A change to the contract changes both the document and the code in the same PR; the contract suite and the import-boundary test ([`architecture.md`](architecture.md#the-package-and-the-images)) hold it.

## Versioning

The version lives in **`pyproject.toml` only**; never hard-code it elsewhere, and never type it on a `git tag` line. Use `git bump` / `git release` from [`dev-tools`](https://github.com/ParkviewLab/dev-tools). See the handbook's [`releases.md`](https://github.com/ParkviewLab/handbook/blob/main/docs/releases.md).

## Releasing

See [`README.md`](../README.md#releasing) for the flow: the CLI commands, what the workflow does, and the back-merge that ends the release.

## AI contributors

Read `docs/northstar.md` first, and follow the behavioural contract in the handbook's [`ai-collaboration.md`](https://github.com/ParkviewLab/handbook/blob/main/docs/ai-collaboration.md) (notably: merging/tagging/releasing need an explicit, per-release go-ahead). The northstar leads: a change that alters intent amends it in the same PR, and an unintended disagreement between it and the code is a defect in the code.
