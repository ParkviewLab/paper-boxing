# SPDX-FileCopyrightText: 2026 Gary Frattarola <garyf@parkviewlab.ai>
#
# SPDX-License-Identifier: MIT OR Apache-2.0

"""The frontend's header: the ParkviewLab logo (the official file, its font embedded, nothing fetched)
and, beside it, the label with the name and the running version, read from the package's one version
source and set in the brand's wordmark face, Michroma, supplied to the page from the same vendored file
as data; the name at 21 px, the version at 12 px on its baseline, and neither the logo nor the label
able to shrink."""

from __future__ import annotations

import ast
import base64
import inspect
import re
import xml.etree.ElementTree as ET
from importlib import resources

from nicegui import ui
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


def _label(user: User) -> ui.html:
    """The header label element on the page the user is looking at."""
    element = user.find(marker="brand-name").elements.pop()
    assert isinstance(element, ui.html)
    return element


def _parts(label: ui.html) -> tuple[ET.Element, ET.Element, ET.Element]:
    """The label's markup parsed: the element itself, the name span and the version span."""
    root = ET.fromstring(f"<div>{label.content}</div>")
    name, version = list(root)
    return root, name, version


def _text(label: ui.html) -> str:
    """The label's text as a test or a screen reader reads it: the markup's text nodes joined."""
    return "".join(_parts(label)[0].itertext())


async def test_header_names_the_application_and_the_running_version(user: User) -> None:
    await support.sign_in(user, *support.ADMIN)
    await user.should_see(marker="brand-name")
    assert _text(_label(user)) == f"paper-boxing v{config.VERSION}"
    await user.open("/tokens")
    await user.should_see(marker="brand-name")
    assert _text(_label(user)) == f"paper-boxing v{config.VERSION}"


def test_header_version_is_the_one_the_package_metadata_gives() -> None:
    """The label's version is `paper_boxing.common.config.VERSION` itself, not a second copy: the layout
    module imports that name from there, and the function that spells the label uses the name rather than
    the value. What the rendered text is, the test above asserts."""
    assert layout.VERSION is config.VERSION
    module = ast.parse(inspect.getsource(layout))
    imports = {
        (node.module, alias.name)
        for node in ast.walk(module)
        if isinstance(node, ast.ImportFrom)
        for alias in node.names
    }
    assert ("paper_boxing.common.config", "VERSION") in imports
    label = inspect.getsource(layout.brand_label_html)
    assert "VERSION" in label
    assert config.VERSION not in label


async def test_header_label_is_the_name_at_21_px_and_the_version_at_12_px_on_its_baseline(user: User) -> None:
    """Two parts in one element: the name at 21 px and the version at 12 px, both in Michroma with the
    letter-spacing the label had, a real space between them in the markup, and the element a flex row
    aligned on the baseline with the gap chosen against the preview; white at opacity .85 as before."""
    await support.sign_in(user, *support.ADMIN)
    await user.should_see(marker="brand-name")
    label = _label(user)
    _, name, version = _parts(label)
    assert name.tag == "span" and name.text == "paper-boxing"
    assert version.tag == "span" and version.text == f"v{config.VERSION}"
    assert name.tail == " " and version.tail is None
    for part, size in ((name, 21), (version, 12)):
        style = part.attrib["style"]
        assert "font-family:'Michroma'" in style
        assert f"font-size:{size}px" in style
        assert "letter-spacing:.02em" in style
    assert label._style["display"] == "flex"
    assert label._style["align-items"] == "baseline"
    assert label._style["gap"] == ".5em"
    assert label._style["opacity"] == ".85"
    assert "text-white" in label._classes


async def test_logo_and_label_cannot_shrink(user: User) -> None:
    """The logo, its link and the label are flex items that keep their size (`shrink-0`), so a header
    narrower than its contents wraps its row rather than narrowing the logo."""
    await support.sign_in(user, *support.ADMIN)
    await user.should_see(marker="brand-logo")
    logo = user.find(marker="brand-logo").elements.pop()
    assert "shrink-0" in logo._classes
    link = logo.parent_slot.parent
    assert isinstance(link, ui.link) and "shrink-0" in link._classes
    assert "shrink-0" in _label(user)._classes


async def test_michroma_comes_from_the_vendored_face_as_data(user: User) -> None:
    """The face reaches the page as one @font-face in the head whose data is the vendored woff2 itself,
    the same declaration the logo's SVG carries; the rendered page names neither Google Fonts host."""
    await support.sign_in(user, *support.ADMIN)
    await user.should_see(marker="brand-name")

    vendored = resources.files("paper_boxing.frontend").joinpath("brand", "fonts", "michroma-latin.woff2")
    data = base64.b64encode(vendored.read_bytes()).decode("ascii")
    face = f"@font-face{{font-family:'Michroma';font-style:normal;font-weight:400;src:url('data:font/woff2;base64,{data}') format('woff2');}}"
    head = user.client.head_html
    assert head.count(face) == 1
    assert face in layout.logo_svg()

    page = (await user.http_client.get("/")).text
    assert face in page
    assert "fonts.googleapis.com" not in page and "fonts.gstatic.com" not in page
    # NiceGUI's own stylesheets are @imported from the frontend itself; nothing is imported from elsewhere.
    assert not re.search(r"@import\s+url\(\s*['\"]?(https?:)?//", page)
