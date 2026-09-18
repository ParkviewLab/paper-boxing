# SPDX-FileCopyrightText: 2026 Gary Frattarola <garyf@parkviewlab.ai>
#
# SPDX-License-Identifier: MIT OR Apache-2.0

"""The compose file's inline nginx configuration, read as text and held to
`tests/_site_server_types.py`: the `types` block maps exactly the table's
extensions, the default type is the text type, the charset is written into
the text type and no `charset` directive is set (its `charset_types` always
includes `text/html`, which would give every page a charset header), and the
stock `mime.types` is included before the block so that the block extends
it. This is the cheap check; the integration tier proves the same through
the real nginx.
"""

from __future__ import annotations

import re
import shlex
import textwrap
from pathlib import Path

from tests._site_server_types import (
    ADDED_TYPES,
    CHARSET,
    DEFAULT_TYPE,
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


def test_the_types_block_maps_exactly_the_table() -> None:
    assert types_block(nginx_config()) == ADDED_TYPES


def test_the_default_type_is_the_text_type() -> None:
    assert DEFAULT_TYPE == TEXT_TYPE
    assert f'default_type "{DEFAULT_TYPE}";' in directives(nginx_config())


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
    assert include < config.index("default_type ")


def test_the_block_names_the_reason_and_the_documentation() -> None:
    config = nginx_config()
    assert "application/octet-stream" in config
    assert "docs/architecture.md" in config


def test_the_table_is_consistent_with_itself() -> None:
    """No text extension is a stock page, data or binary type; the deliberate overrides are text; nothing is listed twice."""
    assert len(TEXT_PLAIN_EXTENSIONS) == len(set(TEXT_PLAIN_EXTENSIONS))
    assert set(ADDED_TYPES).isdisjoint(STOCK_UNCHANGED)
    assert set(STOCK_OVERRIDDEN) <= set(TEXT_PLAIN_EXTENSIONS)
