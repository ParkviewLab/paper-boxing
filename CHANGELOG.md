<!--
SPDX-FileCopyrightText: 2026 Gary Frattarola <garyf@parkviewlab.ai>

SPDX-License-Identifier: MIT OR Apache-2.0
-->

# Changelog

All notable changes to this project are recorded here. Each release entry has two parts: a **Highlights** paragraph, generated at release time by an Anthropic-API call (dev-tools' `generate-changelog`, run by the release workflow), and the **categorized changes**, a list of merged commits since the previous tag grouped by [Conventional Commit](https://www.conventionalcommits.org/) prefix, produced by [git-cliff](https://git-cliff.org/) using `cliff.toml`.

The release workflow on every tag push regenerates both, commits the new section here, and uses the same content as the GitHub Release body.

<!--
Keep-a-Changelog ordering: [Unreleased] at the top, then newest released version, then older versions. generate-changelog inserts new "## [vX.Y.Z] - YYYY-MM-DD" sections directly below [Unreleased]. Don't remove the marker.
-->

## [Unreleased]

## [v0.4.0] - 2026-09-18

### Highlights

The header label beside the logo now renders in Michroma, with the project name at 21 px and the version at 12 px sharing a baseline, and the logo, link and label no longer shrink so a narrow header wraps its row instead.

### Docs

- V0.3.0 [skip ci] (9a8b26a)

### Features

- Set the header label in Michroma, the version on the name's baseline (#15) (82308c9)

## [v0.3.0] - 2026-09-17

### Highlights

The frontend header now shows the running version alongside the project name, sourced from the same package metadata that `/health` reports. Documentation and architecture notes were updated to describe the header and record the design decision behind it.

### Docs

- V0.2.0 [skip ci] (34f1ee4)

### Features

- Show the running version in the frontend header (#14) (6ad40ae)

## [v0.2.0] - 2026-09-17

### Highlights

The site server now displays source, configuration and plain-text files in the browser instead of downloading them, with UTF-8 declared in the content type; files whose names carry an unknown extension keep nginx's stock default and download as before. Documentation gains a link to the documentation site under the README title, records a third intended use (a page generated on another machine and served unchanged), and points the status line at the releases page rather than a pinned version.

### Docs

- V0.1.1 [skip ci] (4d078ff)
- Name the documentation site under the README's title (#10) (4090df8)
- The README's status names the releases page, not a version that goes stale (#11) (5653018)
- A page generated elsewhere is a third use of the same shape (#12) (0a98c44)

### Features

- Serve source, configuration and text files as text the browser displays (#13) (12eb3dd)

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

