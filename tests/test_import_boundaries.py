# SPDX-FileCopyrightText: 2026 Gary Frattarola <garyf@parkviewlab.ai>
#
# SPDX-License-Identifier: MIT OR Apache-2.0

"""Component boundaries, enforced with `ast` (docs/architecture.md, "The package and the images").

`common` imports none of the three components; `backend`, `frontend` and
`mcp` may import `common` but never each other; and the top-level package
imports `common` only, since every image imports it.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

SRC = Path(__file__).resolve().parent.parent / "src" / "paper_boxing"
COMPONENTS = ("backend", "frontend", "mcp")

# subpackage -> the sibling subpackages it may import
ALLOWED: dict[str, frozenset[str]] = {
    "common": frozenset(),
    "backend": frozenset({"common"}),
    "frontend": frozenset({"common"}),
    "mcp": frozenset({"common"}),
    "": frozenset({"common"}),  # paper_boxing/__init__.py and any other top-level module
}


def _subpackage_of(path: Path) -> str:
    rel = path.relative_to(SRC)
    return rel.parts[0] if len(rel.parts) > 1 else ""


def _imported_subpackages(path: Path) -> set[tuple[str, int]]:
    """Every `paper_boxing.<sub>` a module imports, absolutely or relatively, with the line number."""
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    found: set[tuple[str, int]] = set()
    own = _subpackage_of(path)
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                parts = alias.name.split(".")
                if parts[0] == "paper_boxing" and len(parts) > 1:
                    found.add((parts[1], node.lineno))
        elif isinstance(node, ast.ImportFrom):
            if node.level == 0:
                parts = (node.module or "").split(".")
                if parts[0] == "paper_boxing":
                    if len(parts) > 1:
                        found.add((parts[1], node.lineno))
                    else:
                        found.update((alias.name, node.lineno) for alias in node.names)
            else:
                # A relative import: level 1 from inside a subpackage stays in it; anything
                # deeper reaches a sibling or the package root, which is what we resolve here.
                depth = len(path.relative_to(SRC).parts) - 1  # directories below paper_boxing/
                if node.level > depth:
                    parts = (node.module or "").split(".") if node.module else []
                    if node.level == depth + 1 and parts:
                        found.add((parts[0], node.lineno))
                    elif node.level == depth + 1:
                        found.update((alias.name, node.lineno) for alias in node.names)
                elif own:
                    found.add((own, node.lineno))
    return found


def _modules() -> list[Path]:
    return sorted(p for p in SRC.rglob("*.py"))


@pytest.mark.parametrize("module", _modules(), ids=lambda p: str(p.relative_to(SRC)))
def test_module_imports_only_what_its_subpackage_may(module: Path) -> None:
    own = _subpackage_of(module)
    allowed = ALLOWED[own] | ({own} if own else set())
    offending = sorted(
        (sub, line)
        for sub, line in _imported_subpackages(module)
        if sub in (*COMPONENTS, "common") and sub not in allowed
    )
    assert not offending, (
        f"{module.relative_to(SRC)} imports {offending}; {own or 'the package root'} may import only {sorted(allowed)}"
    )


def test_every_subpackage_is_present() -> None:
    for name in (*COMPONENTS, "common"):
        assert (SRC / name / "__init__.py").is_file(), name


def test_the_rule_catches_a_violation(tmp_path: Path) -> None:
    """The walker itself is checked: a frontend module importing the backend is reported."""
    fake = tmp_path / "frontend" / "bad.py"
    fake.parent.mkdir()
    fake.write_text("from paper_boxing.backend import app\nimport paper_boxing.mcp.server\n")
    tree = ast.parse(fake.read_text())
    imported = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module:
            imported.add(node.module.split(".")[1])
        elif isinstance(node, ast.Import):
            imported.update(alias.name.split(".")[1] for alias in node.names)
    assert imported == {"backend", "mcp"}
