# SPDX-FileCopyrightText: 2026 Gary Frattarola <garyf@parkviewlab.ai>
#
# SPDX-License-Identifier: MIT OR Apache-2.0

"""The frontend's header: the ParkviewLab logo (the official file, its font embedded, nothing fetched)
and, beside it, the name with the running version, read from the package's one version source."""

from __future__ import annotations

import ast
import inspect
import xml.etree.ElementTree as ET

from nicegui.testing import User

from paper_boxing.common import config
from paper_boxing.frontend import layout
from tests import frontend_support as support


def test_logo_is_the_official_file_with_the_font_embedded() -> None:
    svg = layout.logo_svg(60)
    root = ET.fromstring(svg)
    assert root.tag.endswith("svg")
    assert root.attrib["height"] == "60" and root.attrib["width"] == "167"
    assert root.attrib["viewBox"] == "55 100 515 185"
    assert "Parkview Lab" in svg and "PARKVIEW" in svg and "LAB" in svg
    assert "data:font/woff2;base64," in svg


def test_logo_loads_nothing_from_the_network() -> None:
    for variant in ("dark", "white"):
        svg = layout.logo_svg(variant=variant)
        assert "fonts.googleapis" not in svg and "@import" not in svg
        assert "http" not in svg.replace("http://www.w3.org/2000/svg", "")
        assert "<script" not in svg


async def test_header_names_the_application_and_the_running_version(user: User) -> None:
    await support.sign_in(user, *support.ADMIN)
    await user.should_see(marker="brand-name")
    assert user.find(marker="brand-name").elements.pop().text == f"paper-boxing v{config.VERSION}"
    await user.open("/tokens")
    await user.should_see(f"paper-boxing v{config.VERSION}")


def test_header_version_is_the_one_the_package_metadata_gives() -> None:
    """The label's version is `paper_boxing.common.config.VERSION` itself, not a second copy: the layout
    module imports that name from there, and `frame()` uses the name rather than the value. What the
    rendered text is, the test above asserts; how `frame()` spells it is its own affair."""
    assert layout.VERSION is config.VERSION
    module = ast.parse(inspect.getsource(layout))
    imports = {
        (node.module, alias.name)
        for node in ast.walk(module)
        if isinstance(node, ast.ImportFrom)
        for alias in node.names
    }
    assert ("paper_boxing.common.config", "VERSION") in imports
    header = inspect.getsource(layout.frame)
    assert "VERSION" in header
    assert config.VERSION not in header
