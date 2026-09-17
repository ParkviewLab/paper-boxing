# SPDX-FileCopyrightText: 2026 Gary Frattarola <garyf@parkviewlab.ai>
#
# SPDX-License-Identifier: MIT OR Apache-2.0

"""The naming rules of the contract: site slugs and file paths.

These are pure functions with no filesystem access, shared by the backend (the
one enforcer, which additionally resolves paths on disk and checks containment
as deco-assaying's `safe_subpath` does), the fake backend, the frontend (to
preview a slug or reject a path before sending it) and the MCP server.
"""

from __future__ import annotations

import re
import unicodedata

SLUG_MAX_LENGTH = 63
SLUG_RE = re.compile(r"^[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?$")

PATH_MAX_LENGTH = 1024
SEGMENT_MAX_BYTES = 255
# Control characters (including NUL) and the backslash are never part of a path.
_FORBIDDEN_PATH_CHARS = re.compile(r"[\\\x00-\x1f\x7f]")


def slug_from_name(name: str) -> str:
    """Derive a site's slug from its display name.

    Lowercase ASCII letters, digits and single hyphens; accents are stripped
    (NFKD), every other run of characters becomes one hyphen, leading and
    trailing hyphens go, and the result is cut to 63 characters. Raises
    `ValueError` when nothing usable remains.
    """
    ascii_name = unicodedata.normalize("NFKD", name).encode("ascii", "ignore").decode("ascii")
    slug = re.sub(r"[^a-z0-9]+", "-", ascii_name.lower()).strip("-")
    slug = slug[:SLUG_MAX_LENGTH].strip("-")
    if not slug:
        raise ValueError(f"no slug can be derived from the name {name!r}")
    return slug


def validate_slug(slug: str) -> str:
    """Return `slug` unchanged when it is a valid site slug; raise `ValueError` otherwise."""
    if not SLUG_RE.match(slug):
        raise ValueError(
            f"invalid site slug {slug!r}: lowercase letters, digits and hyphens, 1..63 characters"
        )
    return slug


def validate_site_path(path: str, *, allow_root: bool = False) -> str:
    """Normalise and validate a path inside a site; return the canonical form.

    The canonical form is relative, `/`-separated, with no leading, trailing
    or doubled slashes. Rejected, with `ValueError`: `.` and `..` segments,
    backslashes, control characters, a segment longer than 255 bytes, a path
    longer than 1024 characters, and the empty path unless `allow_root` is set
    (listing and archiving accept the site root; a file operation does not).
    """
    if _FORBIDDEN_PATH_CHARS.search(path):
        raise ValueError("path contains a control character or a backslash")
    stripped = path.strip("/")
    if not stripped:
        if allow_root:
            return ""
        raise ValueError("path must name a file or folder inside the site, not the site root")
    if len(stripped) > PATH_MAX_LENGTH:
        raise ValueError(f"path longer than {PATH_MAX_LENGTH} characters")
    segments = stripped.split("/")
    for segment in segments:
        if segment == "":
            raise ValueError("path contains an empty segment (doubled slash)")
        if segment in (".", ".."):
            raise ValueError("path contains a '.' or '..' segment")
        if len(segment.encode("utf-8")) > SEGMENT_MAX_BYTES:
            raise ValueError(f"path segment longer than {SEGMENT_MAX_BYTES} bytes")
    return "/".join(segments)


def path_parent(path: str) -> str:
    """The canonical parent of a canonical path ("" for a top-level entry)."""
    return path.rpartition("/")[0]


def path_name(path: str) -> str:
    """The last segment of a canonical path."""
    return path.rpartition("/")[2]
