# SPDX-FileCopyrightText: 2026 Gary Frattarola <garyf@parkviewlab.ai>
#
# SPDX-License-Identifier: MIT OR Apache-2.0

"""The ParkviewLab logo in the frontend's frame: the official file, its font embedded, nothing fetched."""

from __future__ import annotations

import xml.etree.ElementTree as ET

from paper_boxing.frontend import layout


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
