<!--
SPDX-FileCopyrightText: 2026 Gary Frattarola <garyf@parkviewlab.ai>

SPDX-License-Identifier: MIT OR Apache-2.0
-->

# Changelog

All notable changes to this project are recorded here. Each release entry has two parts: a **Highlights** paragraph, generated at release time by an Anthropic-API call (dev-tools' `generate-changelog`, run by the release workflow), and the **categorized changes**, every pull request merged since the previous tag, by its title under the section its [Conventional Commit](https://www.conventionalcommits.org/) type names, and any commit that reached the release without one, produced by the same script (see the README's Releasing section).

The release workflow on every tag push regenerates both, commits the new section here, and uses the same content as the GitHub Release body.

<!--
Keep-a-Changelog ordering: [Unreleased] at the top, then newest released version, then older versions. generate-changelog inserts new "## [vX.Y.Z] - YYYY-MM-DD" sections directly below [Unreleased]. Don't remove the marker.
-->

## [Unreleased]

## [v0.4.3] - 2026-10-02

### Highlights

This release is documentation and maintenance only, with no change to the application itself: the changelog preamble, deployment guide, decision records and contributing notes were corrected to match current practice, and the repository was brought into line with handbook v2.1.0 and newer pinned dev-tools workflows. The one change readers will notice is on the published docs site, whose index now lists page titles without the "ParkviewLab ·" prefix once the site is rebuilt.

### Docs

- The assembly file cites ci.md's section by its name (#24)
- Bring the changelog preamble, deployment guide and records current before 0.4.3 (#26)

### Maintenance

- Align with handbook v2.1.0 (#25)

## [v0.4.2] - 2026-09-27

### Highlights

The only user-facing change is on the Sites page: the create-site card's hint now reads "The site's address will be based on the display name. Once created, this address can't be changed.", replacing the earlier wording about slugs being derived from the display name. The rest of the release is internal, covering the removal of the retired changelog generator and a move to merge commits with a back-merge pull request.

### Bug fixes

- Plainer wording for the site address on the create-site card (#21)
- The create-site card says the address can't be changed once created (#22)

### Maintenance

- Remove the retired changelog generator (#19)
- Merge commits and the checked back-merge pull request (#20)

## [v0.4.1] - 2026-09-19

### Highlights

This release contains only release-tooling and CI changes, with no user-visible effect on the sites, web UI, or MCP server. The release workflow is now assembled from the handbook's shared parts, which adds a check rejecting tags whose version still carries a `.devN`/`-devN` marker, narrows `packages: write` to the image-building job, and switches changelog generation to dev-tools' shared script. Documentation in the README, contributing guide, and changelog preamble was updated to match.

### Maintenance

- Drop the shallow re-fetch from the version guard (#16)
- Assemble the release workflows from the handbook's parts (#17)
- Generate the changelog with dev-tools' shared script (#18)

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

