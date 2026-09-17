# SPDX-FileCopyrightText: 2026 Gary Frattarola <garyf@parkviewlab.ai>
#
# SPDX-License-Identifier: MIT OR Apache-2.0

"""Entrypoint: `python -m paper_boxing.mcp` -> uvicorn.

Streamable HTTP only. There is no stdio transport: the server runs in the
stack on the home-lab host for every machine on the LAN (docs/design.md
section 3).
"""

import logging

import uvicorn

from paper_boxing.mcp.config import load_config


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
    cfg = load_config()
    uvicorn.run("paper_boxing.mcp.server:app", host=cfg.host, port=cfg.port)


if __name__ == "__main__":
    main()
