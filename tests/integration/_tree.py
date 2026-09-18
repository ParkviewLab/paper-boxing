# SPDX-FileCopyrightText: 2026 Gary Frattarola <garyf@parkviewlab.ai>
#
# SPDX-License-Identifier: MIT OR Apache-2.0

"""The site tree the tier uploads: a small static site generated in memory,
the same for a given label, with a self-contained page of 100 KiB or more.

Nothing is committed as a fixture file (the handbook's testing page). The
PNG is a real image (a valid IHDR, one zlib IDAT of noise, an IEND); the
font is the WOFF2 magic number over noise, because the tier proves that the
bytes come back unchanged with the type nginx maps the extension to, not
that a browser can render the face. `media_type` is the Content-Type nginx
must answer, from its stock `mime.types` plus what the compose file adds
(the table in `tests/_site_server_types.py`; `test_sites_types.py` fetches
every entry of it). A name with a space and a non-ASCII letter is in the
tree, since designers' files have them and every hop must encode them.
"""

from __future__ import annotations

import hashlib
import random
import struct
import zlib
from dataclasses import dataclass

MIN_PAGE_BYTES = 100 * 1024


@dataclass(frozen=True)
class TreeFile:
    path: str
    data: bytes
    media_type: str

    @property
    def sha256(self) -> str:
        return hashlib.sha256(self.data).hexdigest()


def png(width: int, height: int, rng: random.Random) -> bytes:
    """A valid 8-bit RGB PNG of noise; noise does not compress, so the size is predictable."""

    def chunk(tag: bytes, payload: bytes) -> bytes:
        return (
            struct.pack(">I", len(payload))
            + tag
            + payload
            + struct.pack(">I", zlib.crc32(tag + payload) & 0xFFFFFFFF)
        )

    rows = b"".join(b"\x00" + rng.randbytes(width * 3) for _ in range(height))
    header = struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0)
    return (
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", header)
        + chunk(b"IDAT", zlib.compress(rows, 9))
        + chunk(b"IEND", b"")
    )


def _base64(data: bytes) -> str:
    import base64

    return base64.b64encode(data).decode("ascii")


def self_contained_page(title: str, rng: random.Random) -> bytes:
    """A designed page as paper-boxing is meant to hold one: an inline style and script, an inline
    SVG, an image embedded as a data URI, UTF-8 text beyond ASCII, at least 100 KiB in all."""
    image = _base64(png(160, 160, rng))
    rows = "\n".join(
        f"<tr><td>{i}</td><td>{rng.randrange(10**6):06d}</td><td>{'éàü' * (i % 4)}</td></tr>"
        for i in range(120)
    )
    page = f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<title>{title}: design</title>
<style>
  :root {{ --ink: #1f2933; --paper: #f8f5ef; --accent: #b3541e; }}
  body {{ margin: 0 auto; max-width: 52rem; padding: 2rem; font: 16px/1.5 Georgia, serif; color: var(--ink); background: var(--paper); }}
  h1, h2 {{ font-family: Michroma, sans-serif; letter-spacing: .04em; }}
  table {{ border-collapse: collapse; width: 100%; }}
  td, th {{ border-bottom: 1px solid #ccc; padding: .25rem .5rem; text-align: left; }}
  .hero {{ display: grid; grid-template-columns: 1fr 160px; gap: 1rem; align-items: center; }}
  @media (max-width: 40rem) {{ .hero {{ grid-template-columns: 1fr; }} }}
</style>
</head>
<body>
<header class="hero">
  <div>
    <h1>{title}</h1>
    <p>A self-contained page: style, script, vector and raster all inline, so it travels as one file.</p>
    <p>Beyond ASCII: über — naïve — “quoted” — 日本語 — \U0001f642 — &lt;escaped&gt; &amp; 100%.</p>
  </div>
  <img alt="noise" width="160" height="160" src="data:image/png;base64,{image}">
</header>
<section>
  <h2>Structure</h2>
  <svg viewBox="0 0 320 120" width="320" height="120" role="img" aria-label="three boxes">
    <rect x="10" y="20" width="80" height="80" fill="none" stroke="#b3541e" stroke-width="3"/>
    <rect x="120" y="20" width="80" height="80" fill="none" stroke="#1f2933" stroke-width="3"/>
    <rect x="230" y="20" width="80" height="80" fill="#b3541e" opacity=".35"/>
    <path d="M90 60 H120 M200 60 H230" stroke="#1f2933" stroke-width="2"/>
  </svg>
</section>
<section>
  <h2>Table</h2>
  <table>
    <thead><tr><th>#</th><th>value</th><th>text</th></tr></thead>
    <tbody>
{rows}
    </tbody>
  </table>
</section>
<script>
  (function () {{
    "use strict";
    const cells = document.querySelectorAll("tbody td:nth-child(2)");
    let total = 0;
    for (const cell of cells) total += Number(cell.textContent);
    const p = document.createElement("p");
    p.textContent = `Sum of the values: ${{total}} — rendered ${{new Date().toISOString()}}`;
    document.body.appendChild(p);
  }})();
</script>
</body>
</html>
"""
    data = page.encode("utf-8")
    assert len(data) >= MIN_PAGE_BYTES, len(data)
    return data


def site_tree(label: str, *, with_index: bool) -> tuple[TreeFile, ...]:
    """The tree for one site: HTML, CSS, JS (a script and a module), SVG, PNG, WOFF2, a manifest, a text
    file with a space and a non-ASCII letter in its name, and two levels of folders. `with_index`
    puts an `index.html` at the root; without it, nginx lists the root."""
    rng = random.Random(label)
    index = f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<title>{label}</title>
<link rel="stylesheet" href="css/site.css">
<link rel="manifest" href="app.webmanifest">
<script type="module" src="js/module.mjs"></script>
<script src="js/app.js" defer></script>
</head>
<body>
<h1>{label}</h1>
<p>Every link is relative to the site: <a href="docs/design.html">design</a>, <a href="docs/guide/chapter-1.html">guide</a>.</p>
<img src="img/logo.svg" alt="logo" width="64" height="64">
<img src="img/noise.png" alt="noise" width="32" height="32">
</body>
</html>
"""
    css = (
        "@font-face { font-family: Mono; src: url(../fonts/mono.woff2) format('woff2'); }\n"
        "body { font-family: Georgia, serif; margin: 2rem; }\n"
        "code { font-family: Mono, monospace; }\n"
    )
    js = 'document.addEventListener("DOMContentLoaded", () => { console.log("app ready"); });\n'
    mjs = "export const version = 1;\nexport function greet(name) { return `hello ${name}`; }\n"
    svg = (
        '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 64 64">'
        '<rect x="8" y="8" width="48" height="48" fill="none" stroke="#b3541e" stroke-width="4"/>'
        "</svg>\n"
    )
    manifest = f'{{"name": "{label}", "start_url": "./", "display": "standalone"}}\n'
    chapter = (
        "<!doctype html><html lang='en'><head><meta charset='utf-8'><title>Chapter 1</title></head>"
        "<body><h1>Chapter 1</h1><p>Two folders deep.</p></body></html>\n"
    )
    notes = "Plan für über\n- Schritt 1\n- Schritt 2\n"
    files = [
        TreeFile("css/site.css", css.encode(), "text/css"),
        TreeFile("js/app.js", js.encode(), "application/javascript"),
        TreeFile("js/module.mjs", mjs.encode(), "application/javascript"),
        TreeFile("img/logo.svg", svg.encode(), "image/svg+xml"),
        TreeFile("img/noise.png", png(24, 24, rng), "image/png"),
        TreeFile("fonts/mono.woff2", b"wOF2" + rng.randbytes(4096), "font/woff2"),
        TreeFile("app.webmanifest", manifest.encode(), "application/manifest+json"),
        TreeFile("docs/design.html", self_contained_page(label, rng), "text/html"),
        TreeFile("docs/guide/chapter-1.html", chapter.encode(), "text/html"),
        TreeFile("notes/über plan.txt", notes.encode(), "text/plain"),
    ]
    if with_index:
        files.insert(0, TreeFile("index.html", index.encode(), "text/html"))
    return tuple(files)
