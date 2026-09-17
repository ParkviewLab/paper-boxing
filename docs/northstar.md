<!--
SPDX-FileCopyrightText: 2026 Gary Frattarola <garyf@parkviewlab.ai>

SPDX-License-Identifier: MIT OR Apache-2.0
-->

# paper-boxing: northstar

This northstar is the canonical statement of paper-boxing's purpose. It is here to guide every design decision and feature proposal, and each is weighed against it. Where it and any other document disagree, this one is the authority and the other is the thing to fix; where it and the code disagree, the code is wrong. A change of intent is therefore made here first, in the same pull request as the code that follows it, so that no disagreement is ever left standing by accident.

## What it is

paper-boxing is the working space for the early design and specification of big new projects: the place where a project's documents live before it has a repository and a release, and where anything private lives permanently. It is a small site manager for one person's home lab. A site is a folder of static files, served unchanged over plain HTTP on the private LAN at a stable address; a person manages sites and files in a web UI, an agent does the same through an MCP server, and one backend does the writing for both. It runs as four containers: the backend, the frontend, the MCP server, and a stock nginx that serves the sites read-only.

## Why it exists

Early design produces documents that change daily: designed HTML pages, visual explorations, specifications, often with scripts. They are made by people and by agents together, and they need a place that is private, on the LAN, instant to update, and writable by both. A wiki is the wrong shape for them, since it rewrites what it stores and cannot hold a designed page as designed; a repository's documentation site is the right shape only once the project has a repository and ships releases, and it is public. Between the first sketch and the first release, and for everything that must stay private, there was nowhere to put them. paper-boxing is that place, and nothing more.

## Intents

paper-boxing serves three complementary intents: facets of one purpose, presented as peers rather than as one primary and the rest secondary, and mutually reinforcing.

1. **Pages kept exactly as written.** What is uploaded is what is served, byte for byte, at an address that never changes.

2. **Equally usable by a person and an agent, through one backend.** The UI and the MCP server are two clients of the same API, with the same rules enforced in one place.

3. **Sized for one person's home lab, in Docker.** A private LAN, plain HTTP, one operator, a handful of containers; safeguards against accidents, not measures against strangers.

### 1. Pages kept exactly as written

A site is a folder of files, and the site server is a stock nginx that serves that folder read-only. paper-boxing does not render, transform, template or version what it stores; it holds no history and runs nothing on the server side. A replaced file shows at once, and a site's address, `/<site>/` on the site server's port, is part of every link placed elsewhere and never changes once the site exists. This rules out any feature that would make the served page differ from the uploaded one, and it is why the site server is a separate process that cannot write.

### 2. Equally usable by a person and an agent, through one backend

The backend is the only writer of files and of the database, and the one implementation of every rule: path containment, overwrite and delete intent, token scopes. The frontend holds no files and no database; the MCP server holds no token store and confirms every request's token with the backend before a tool runs. An agent holds a token a person created, with a scope the person chose, and the backend enforces that scope whichever client calls. What a person can do in the UI, an agent can do through the tools, and neither can do more than the backend allows.

### 3. Sized for one person's home lab, in Docker

Everything runs in one compose stack on one host, on fixed ports, with one data volume and one version for the three images. The threat model is stated rather than hidden: a private LAN over plain HTTP, where tokens travel unencrypted and there is no lockout, because the operator controls the network. The design spends its care on accidents, since they are what happens on a private LAN: a path that escapes a site, a file overwritten by mistake, a site deleted by a slip. It spends nothing on the public internet or on mutually untrusted users, because it is not for them.

### How the intents reinforce each other

The first intent is possible because of the second: a single writer with fixed rules is what lets the site server be a read-only stock nginx, so nothing can alter a page after it is written. The second is possible because of the third: one operator on a private LAN is what makes a per-user token with a scope, confirmed on every call, an adequate authentication model, without the apparatus a public service would need. And the third holds because of the first: a service that only stores and serves files has few moving parts, which is what keeps it small enough for a home lab. Where the intents meet, the design has chosen the plain option each time: nginx rather than serving from the backend, single-file operations rather than batches, a listing rather than a generated index.

## Axioms

The same axioms support all the intents, from different angles.

1. **One writer.** Only the backend touches the files and the database. Every other component is its client, and every rule lives there once.

2. **Serve what was stored.** No transformation between upload and response, no history, nothing executed on the server side. If a page looks different served than uploaded, that is a defect.

3. **Addresses are permanent.** A site's slug and the site server's port never change; links placed in the wiki keep working for as long as the site exists.

4. **Intent before destruction.** Overwriting needs `overwrite`, deleting a non-empty folder needs `recursive`, deleting a site needs its name typed back. A write is atomic, so a failure leaves the old file whole.

5. **Say what the threat model is.** Plain HTTP on a private LAN is the accepted deployment, and every document says so plainly rather than implying a security that is not there.

6. **Prefer the stock part.** A stock nginx, a stock compose stack, the handbook's shapes for the package and the MCP server; paper-boxing adds code only where no stock part does the job.

## Guiding questions

When making a decision, these are the questions to keep answering:

- Does the served page remain byte-identical to the uploaded file, at the same address?
- Is the rule enforced in the backend, once, for the UI and the MCP server alike?
- Can a person do it in the UI, and an agent through a tool, with the same outcome?
- Does it fit one host, one compose stack, one operator on a private LAN?
- Is the safeguard against an accident, and is it honest about not being a safeguard against a stranger?

## What paper-boxing is not

- **Not a wiki.** BookStack stays the wiki; its pages link to the sites paper-boxing serves. paper-boxing holds files, not articles.
- **Not a CMS or a build system.** It renders nothing, builds nothing and runs nothing on the server side.
- **Not a public host.** It is designed for a private LAN over plain HTTP, not for the internet or for users who do not trust one another.
- **Not a replacement for a released project's GitHub Pages site.** Once a project has a public repository and ships releases, its documentation moves there; paper-boxing is for the time before, and for what stays private.
- **Not a version store.** It keeps no history; a repository does that.

---
<sub>© 2026 Gary Frattarola · Licensed under [MIT](../LICENSE-MIT) OR [Apache-2.0](../LICENSE-APACHE) · part of [ParkviewLab](https://github.com/ParkviewLab)</sub>
