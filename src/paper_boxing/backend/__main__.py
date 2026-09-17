# SPDX-FileCopyrightText: 2026 Gary Frattarola <garyf@parkviewlab.ai>
#
# SPDX-License-Identifier: MIT OR Apache-2.0

"""Entrypoint: `python -m paper_boxing.backend` -> uvicorn."""

import logging

import uvicorn

from paper_boxing.backend.config import load_config


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
    cfg = load_config()
    uvicorn.run("paper_boxing.backend.app:app", host=cfg.host, port=cfg.port)


if __name__ == "__main__":
    main()
