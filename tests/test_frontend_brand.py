# SPDX-FileCopyrightText: 2026 Gary Frattarola <garyf@parkviewlab.ai>
#
# SPDX-License-Identifier: MIT OR Apache-2.0

"""The ParkviewLab mark in the frontend's frame: vendored whole, scaled inline, colours intact."""

from __future__ import annotations

import xml.etree.ElementTree as ET

from paper_boxing.frontend import layout

BRAND_COLOURS = {"#90b095", "#00C2C7", "#004f52"}


def test_mark_is_the_handbook_mark_scaled() -> None:
    svg = layout.mark_svg(44)
    root = ET.fromstring(svg)
    assert root.tag.endswith("svg")
    assert root.attrib["width"] == "44" and root.attrib["height"] == "44"
    assert root.attrib["viewBox"] == "0 0 400 400"
    assert "Parkview Lab" in svg
    fills = {el.attrib.get("fill") for el in root.iter() if el.attrib.get("fill")}
    assert fills >= BRAND_COLOURS


def test_mark_loads_no_network_resources() -> None:
    svg = layout.mark_svg()
    assert "http" not in svg.replace("http://www.w3.org/2000/svg", "")
    assert "<script" not in svg and "@import" not in svg
