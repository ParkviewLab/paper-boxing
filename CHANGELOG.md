<!--
SPDX-FileCopyrightText: 2026 Gary Frattarola <garyf@parkviewlab.ai>

SPDX-License-Identifier: MIT OR Apache-2.0
-->

# Changelog

All notable changes to this project are recorded here. Each release entry has two parts: a **Highlights** paragraph, generated at release time by an Anthropic-API call (see `scripts/generate_changelog.py`), and the **categorized changes**, a list of merged commits since the previous tag grouped by [Conventional Commit](https://www.conventionalcommits.org/) prefix, produced by [git-cliff](https://git-cliff.org/) using `cliff.toml`.

The release workflow on every tag push regenerates both, commits the new section here, and uses the same content as the GitHub Release body.

<!--
Keep-a-Changelog ordering: [Unreleased] at the top, then newest released version, then older versions. generate_changelog.py inserts new "## [vX.Y.Z] - YYYY-MM-DD" sections directly below [Unreleased]. Don't remove the marker.
-->

## [Unreleased]

## [v0.1.1] - 2026-09-17

### Highlights

This release is documentation-only: the hand-off design brief is replaced by architecture.md and decisions.md, and the README, API contract, deployment guide, LICENSING, northstar and CONTRIBUTING are brought into line with the released state. The docs/ folder is also published as a GitHub Pages site, with HTML twins of the northstar and architecture documents.

### Docs

- V0.1.0 [skip ci] (5f1028d)
- Bring README, API contract, deployment guide and LICENSING to the released state (#7) (235f0ae)
- Architecture and decisions in place of the design brief; the northstar at the released state (#8) (342799b)

### Features

- Publish docs/ as the documentation site, with the northstar and architecture twins (#9) (f4ce511)

## [v0.1.0] - 2026-09-17

### Highlights

First tagged release of paper-boxing, a home-lab site manager comprising a FastAPI backend over SQLite, a NiceGUI web frontend, an MCP server exposing eight tools for agents, and an nginx site server, deployed together as a Docker Compose stack on ports 35840–35843. Sites and their files are managed through a shared REST contract by signed-in people or by agents using scoped `pb_` bearer tokens (each confirmed per request at the MCP gate), and served over the LAN by nginx with directory listings where no index.html is present. The release ships a completed deployment guide covering Docker Compose and Portainer, backup and restore, and Claude Code setup, and is exercised end-to-end by an integration tier that drives the built stack through the API, MCP and nginx.

