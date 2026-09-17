# SPDX-FileCopyrightText: 2026 Gary Frattarola <garyf@parkviewlab.ai>
#
# SPDX-License-Identifier: MIT OR Apache-2.0

"""The ParkviewLab logo in the frontend's frame: the mark vendored whole, Michroma embedded, no network."""

from __future__ import annotations

import xml.etree.ElementTree as ET

from paper_boxing.frontend import layout

BRAND_COLOURS = {"#90b095", "#00C2C7", "#004f52"}


def test_mark_is_the_handbook_mark_scaled() -> None:
    svg = layout.mark_svg(56)
    root = ET.fromstring(svg)
    assert root.tag.endswith("svg")
    assert root.attrib["height"] == "56" and root.attrib["width"] == "87"
    assert root.attrib["viewBox"] == "60 95 280 180"
    assert "Parkview Lab" in svg
    fills = {el.attrib.get("fill") for el in root.iter() if el.attrib.get("fill")}
    assert fills >= BRAND_COLOURS


def test_brand_loads_nothing_from_the_network() -> None:
    css = layout.brand_css()
    assert "data:font/woff2;base64," in css
    assert "fonts.googleapis" not in css and "@import" not in css and "http" not in css
    svg = layout.mark_svg()
    assert "http" not in svg.replace("http://www.w3.org/2000/svg", "")
    assert "<script" not in svg
