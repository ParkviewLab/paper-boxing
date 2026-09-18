# SPDX-FileCopyrightText: 2026 Gary Frattarola <garyf@parkviewlab.ai>
#
# SPDX-License-Identifier: MIT OR Apache-2.0

"""The compose file's inline nginx configuration, the source of what the site
server answers, read as text and held to the check in
`tests/_site_server_types.py`: the `types` block maps exactly the table's
extensions, the server block sets no `default_type` of its own (so an
extension in no table keeps nginx's stock `application/octet-stream`), the
one `default_type` is the text type inside the location that matches a name
with no extension, that location repeats `location /`'s `try_files`, the
charset is written into the text type and no `charset` directive is set (its
`charset_types` always includes `text/html`, which would give every page a
charset header), and the stock `mime.types` is included before the block so
that the block extends it. This is the cheap check; the integration tier
proves the same through the real nginx.
"""

from __future__ import annotations

import re
import shlex
import textwrap
from pathlib import Path

from tests._site_server_types import (
    ADDED_TYPES,
    CHARSET,
    EXTENSIONLESS_LOCATION,
    EXTENSIONLESS_LOCATION_DIRECTIVES,
    EXTENSIONLESS_LOCATION_MATCHES,
    EXTENSIONLESS_LOCATION_MISSES,
    NO_TABLE_EXTENSIONS,
    STOCK_DEFAULT_TYPE,
    STOCK_OVERRIDDEN,
    STOCK_UNCHANGED,
    TEXT_PLAIN_EXTENSIONS,
    TEXT_TYPE,
)

ROOT = Path(__file__).resolve().parent.parent


def nginx_config() -> str:
    """The `content` of the `nginx-sites` config in docker-compose.yml, dedented, with `$$` unescaped."""
    lines = (ROOT / "docker-compose.yml").read_text(encoding="utf-8").splitlines()
    start = next(i for i, line in enumerate(lines) if line.strip() == "content: |")
    indent = len(lines[start]) - len(lines[start].lstrip())
    block: list[str] = []
    for line in lines[start + 1 :]:
        if line.strip() and len(line) - len(line.lstrip()) <= indent:
            break
        block.append(line)
    return textwrap.dedent("\n".join(block)).replace("$$", "$")


def types_block(config: str) -> dict[str, str]:
    """The extension-to-type mapping of the `types { ... }` block, comments stripped; a type may be quoted."""
    match = re.search(r"\btypes\s*\{(.*?)\}", config, re.S)
    assert match, "no types block in the nginx configuration"
    mapping: dict[str, str] = {}
    for raw in match.group(1).splitlines():
        line = raw.split("#", 1)[0].strip().rstrip(";").strip()
        if not line:
            continue
        media_type, *extensions = shlex.split(line)
        for extension in extensions:
            assert extension not in mapping, f"{extension} is listed twice in the types block"
            mapping[extension] = media_type
    return mapping


def directives(config: str) -> list[str]:
    """Every directive of the configuration as one normalised line, comments and blank lines removed."""
    return [
        " ".join(line.split("#", 1)[0].split())
        for line in config.splitlines()
        if line.split("#", 1)[0].strip()
    ]


def locations(config: str) -> dict[str, list[str]]:
    """Each `location` block's header (what follows the word, before the brace) to its directives."""
    blocks: dict[str, list[str]] = {}
    for match in re.finditer(r"^\s*location\s+(.+?)\s*\{(.*?)^\s*\}", config, re.S | re.M):
        header = match.group(1).strip()
        assert header not in blocks, f"location {header} appears twice"
        blocks[header] = directives(match.group(2))
    return blocks


def test_the_types_block_maps_exactly_the_table() -> None:
    assert types_block(nginx_config()) == ADDED_TYPES


def test_the_server_block_leaves_the_stock_default_type_in_force() -> None:
    """No `default_type` outside the extensionless location: an extension in no table downloads as before."""
    config = nginx_config()
    outside = re.sub(r"location\s+.+?\{.*?^\s*\}", "", config, flags=re.S | re.M)
    assert not re.search(r"^\s*default_type\s", outside, re.M), "the server block must not set default_type"
    assert STOCK_DEFAULT_TYPE not in types_block(config).values(), "nothing is pinned to the stock default"


def test_the_extensionless_location_serves_text_and_keeps_the_root_locations_behaviour() -> None:
    """The regex location beside `location /`: the text default type, and the same `try_files`."""
    blocks = locations(nginx_config())
    assert set(blocks) == {"/", f'~ "{EXTENSIONLESS_LOCATION}"'}
    assert blocks["/"] == ["try_files $uri $uri/ =404;"]
    assert blocks[f'~ "{EXTENSIONLESS_LOCATION}"'] == list(EXTENSIONLESS_LOCATION_DIRECTIVES)
    assert f'default_type "{TEXT_TYPE}";' in EXTENSIONLESS_LOCATION_DIRECTIVES
    assert blocks["/"][0] in EXTENSIONLESS_LOCATION_DIRECTIVES


def test_the_extensionless_pattern_matches_a_name_without_an_extension_and_nothing_else() -> None:
    """The pattern is plain enough for PCRE and Python's `re` to agree on it; the integration tier proves nginx's reading."""
    pattern = re.compile(EXTENSIONLESS_LOCATION)
    for uri in EXTENSIONLESS_LOCATION_MATCHES:
        assert pattern.search(uri), uri
    for uri in EXTENSIONLESS_LOCATION_MISSES:
        assert not pattern.search(uri), uri


def test_the_charset_is_in_the_text_type_and_no_charset_directive_is_set() -> None:
    """`charset` would add the charset to `text/html` as well, whatever `charset_types` names."""
    assert f"text/plain; charset={CHARSET}" == TEXT_TYPE
    config = nginx_config()
    assert not re.search(r"^\s*charset(_types)?\s", config, re.M), "the charset module must stay off"
    text_types = {
        media_type for media_type in types_block(config).values() if media_type.startswith("text/plain")
    }
    assert text_types == {TEXT_TYPE}


def test_the_stock_table_is_included_before_the_block_extends_it() -> None:
    config = nginx_config()
    include = config.index("include /etc/nginx/mime.types;")
    assert include < config.index("types {")


def test_the_block_names_the_reason_and_the_documentation() -> None:
    config = nginx_config()
    assert "application/octet-stream" in config
    assert "docs/architecture.md" in config


def test_the_table_is_consistent_with_itself() -> None:
    """No text extension is a stock page, data or binary type; the deliberate overrides are text; nothing is listed twice."""
    assert len(TEXT_PLAIN_EXTENSIONS) == len(set(TEXT_PLAIN_EXTENSIONS))
    assert set(ADDED_TYPES).isdisjoint(STOCK_UNCHANGED)
    assert set(STOCK_OVERRIDDEN) <= set(TEXT_PLAIN_EXTENSIONS)
    assert set(NO_TABLE_EXTENSIONS).isdisjoint(ADDED_TYPES)
    assert set(NO_TABLE_EXTENSIONS).isdisjoint(STOCK_UNCHANGED)
    assert "map" in NO_TABLE_EXTENSIONS
