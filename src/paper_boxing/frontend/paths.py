# SPDX-FileCopyrightText: 2026 Gary Frattarola <garyf@parkviewlab.ai>
#
# SPDX-License-Identifier: MIT OR Apache-2.0

"""The frontend's own paths, in one table: the sign-in page, what the
middleware admits without a session, what it treats as a fetch rather than a
page, and the download route. `app.py` registers the route, `auth.py` applies
the exemptions and the site page builds its download links from here, so the
three cannot drift apart. A leaf: it imports nothing from the package.
"""

from __future__ import annotations

from urllib.parse import quote

LOGIN_PATH = "/login"

DOWNLOAD_PREFIX = "/download"
DOWNLOAD_ROUTE = f"{DOWNLOAD_PREFIX}/{{slug}}/{{path:path}}"

# Paths that need no session: the sign-in page, the ops endpoints, and everything NiceGUI
# serves for itself (static assets, the socket, uploads).
PUBLIC_PATHS = frozenset({LOGIN_PATH, "/health", "/admin/version", "/favicon.ico"})
PUBLIC_PREFIXES = ("/_nicegui", "/socket.io")

# Paths a browser fetches rather than shows: without a session they get a 401 in the
# contract's body, never a redirect to the sign-in page (which would be saved as the file).
FETCH_PREFIXES = (DOWNLOAD_PREFIX,)


def is_public(path: str) -> bool:
    return path in PUBLIC_PATHS or path.startswith(PUBLIC_PREFIXES)


def is_fetch(path: str) -> bool:
    return path.startswith(FETCH_PREFIXES)


def download_url(slug: str, path: str) -> str:
    """The download route for one file, with the path's slashes kept as separators."""
    return f"{DOWNLOAD_PREFIX}/{quote(slug, safe='')}/{quote(path, safe='/')}"
