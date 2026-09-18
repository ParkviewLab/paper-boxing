# SPDX-FileCopyrightText: 2026 Gary Frattarola <garyf@parkviewlab.ai>
#
# SPDX-License-Identifier: MIT OR Apache-2.0

"""The Content-Type the site server answers for each file extension. The
source is the `types` block of the compose file's inline nginx
configuration, which is what runs; this table is the check on it, written
out by group as the decision lists the extensions.
`tests/test_site_server_config.py` parses the block and holds it to this
table; `tests/integration/test_sites_types.py` uploads a file of every
extension here and fetches it through the real nginx, which is what proves
the behaviour.

The rule (docs/architecture.md, "The site server"): source code,
configuration, data and plain-text files are served as `text/plain;
charset=utf-8`, so the browser displays them; a file with no extension, or
with an extension in no table, is served so too (`default_type`); `map`,
which the stock table lacks, is given the stock default explicitly so that a
source map is served as before; the charset is written into the type rather
than set with nginx's `charset` directive, whose `charset_types` always
includes `text/html`, so no page, style sheet or script gains a charset
header that could override its own declaration; everything else the stock
`mime.types` covers keeps its type.
"""

from __future__ import annotations

# The groups as the decision lists them, in its order; the docs name the same groups.
TEXT_PLAIN_GROUPS: dict[str, tuple[str, ...]] = {
    "Python": ("py", "pyi", "pyw", "pyx", "pxd"),
    "TypeScript and JSX": ("ts", "tsx", "mts", "cts", "jsx"),
    "Rust": ("rs",),
    "C and C++": ("c", "h", "cc", "cpp", "cxx", "hh", "hpp", "hxx", "inl", "ipp", "tpp"),
    "Swift and Objective-C": ("swift", "m", "mm"),
    "Other languages": (
        "go",
        "java",
        "kt",
        "kts",
        "cs",
        "rb",
        "lua",
        "pl",
        "pm",
        "php",
        "sql",
        "r",
        "jl",
        "zig",
        "dart",
        "scala",
        "hs",
    ),
    "Shell and build": ("sh", "bash", "zsh", "fish", "ps1", "cmake", "mk", "mak", "gradle", "bzl", "nix"),
    "Configuration and data": (
        "yaml",
        "yml",
        "toml",
        "ini",
        "cfg",
        "conf",
        "env",
        "properties",
        "json5",
        "jsonc",
        "jsonl",
        "ndjson",
        "csv",
        "tsv",
        "lock",
        "editorconfig",
        "gitignore",
        "gitattributes",
        "dockerignore",
    ),
    "Documents and text": (
        "txt",
        "md",
        "markdown",
        "rst",
        "adoc",
        "org",
        "tex",
        "bib",
        "log",
        "diff",
        "patch",
    ),
    "Schemas and interface definitions": (
        "proto",
        "graphql",
        "gql",
        "thrift",
        "avsc",
        "fbs",
        "capnp",
        "idl",
        "x",
    ),
    "Diagram sources": ("dot", "gv", "mmd", "puml", "d2"),
    "XML schemas and tooling": ("xdr", "xsd", "dtd", "rng", "rnc", "sch", "wsdl", "xslt"),
    "Robotics": ("urdf", "xacro", "srdf", "sdf", "launch", "msg", "srv", "action"),
}

TEXT_PLAIN_EXTENSIONS: tuple[str, ...] = tuple(ext for group in TEXT_PLAIN_GROUPS.values() for ext in group)

# Extensions whose file is conventionally the whole name (`.gitignore`); nginx takes the
# part after the last dot as the extension, so the name is the extension.
DOT_FILE_EXTENSIONS: tuple[str, ...] = ("env", "editorconfig", "gitignore", "gitattributes", "dockerignore")

# What the compose file adds beside the text types.
ADDED_JAVASCRIPT: tuple[str, ...] = ("mjs", "cjs")
ADDED_MANIFEST: dict[str, str] = {"webmanifest": "application/manifest+json"}

# An extension the stock table lacks that the block pins to the stock default on purpose, so
# that it does not fall to `default_type`: a source map is served exactly as it was before the
# text types were added (the decision lists `map` among the types to stay unchanged).
KEPT_STOCK_DEFAULT: dict[str, str] = {"map": "application/octet-stream"}

# The one text type, with its charset in the type itself: the exact Content-Type header.
CHARSET = "utf-8"
TEXT_TYPE = f"text/plain; charset={CHARSET}"

# The whole `types` block of the compose file, extension to the exact type as written: the
# static check holds the block to exactly this, and the integration tier fetches every entry.
ADDED_TYPES: dict[str, str] = {
    **dict.fromkeys(ADDED_JAVASCRIPT, "application/javascript"),
    **ADDED_MANIFEST,
    **KEPT_STOCK_DEFAULT,
    **dict.fromkeys(TEXT_PLAIN_EXTENSIONS, TEXT_TYPE),
}

# Entries of the stock `mime.types` that the block overrides on purpose; nginx reports each as
# a duplicate extension at start-up and takes the later mapping.
STOCK_OVERRIDDEN: dict[str, str] = {
    "txt": "text/plain",
    "ts": "video/mp2t",
    "pl": "application/x-perl",
    "pm": "application/x-perl",
}

# A file with no extension, or with one in no table, is served as text too.
DEFAULT_TYPE = TEXT_TYPE

# Types of the stock `mime.types` (nginx 1.30, `nginx:stable-alpine`) that must not change:
# the page, style, script, data and manifest types a site is made of, and a sample of the
# images, fonts and other binary types.
STOCK_UNCHANGED: dict[str, str] = {
    "html": "text/html",
    "htm": "text/html",
    "css": "text/css",
    "js": "application/javascript",
    "svg": "image/svg+xml",
    "wasm": "application/wasm",
    "json": "application/json",
    "xml": "text/xml",
    "png": "image/png",
    "jpg": "image/jpeg",
    "jpeg": "image/jpeg",
    "gif": "image/gif",
    "webp": "image/webp",
    "avif": "image/avif",
    "ico": "image/x-icon",
    "woff": "font/woff",
    "woff2": "font/woff2",
    "eot": "application/vnd.ms-fontobject",
    "pdf": "application/pdf",
    "zip": "application/zip",
    "mp4": "video/mp4",
    "mp3": "audio/mpeg",
    "bin": "application/octet-stream",
    "exe": "application/octet-stream",
}

# Extensions in no table, stock or added, that a site may hold: served with the default type.
# The stock table has no `ttf` or `otf` (it has `woff` and `woff2`), so those fonts serve as
# text; a browser loads a font whatever the type says. The same holds for every other
# extension the stock table lacks (`gz`, `tar`, `wav`, `sqlite`, `pyc`), which is the accepted
# cost the documents name.
NO_TABLE_EXTENSIONS: tuple[str, ...] = ("ttf", "otf", "unknownext")

# Names with no extension at all: served with the default type, so they display.
EXTENSIONLESS_NAMES: tuple[str, ...] = ("README", "LICENSE", "Makefile", "Dockerfile", "bin/deploy")


def expected_content_type(media_type: str) -> str:
    """The exact Content-Type header for a media type: the charset on `text/plain` and on nothing else."""
    return TEXT_TYPE if media_type == "text/plain" else media_type
