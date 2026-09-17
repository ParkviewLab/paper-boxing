# SPDX-FileCopyrightText: 2026 Gary Frattarola <garyf@parkviewlab.ai>
#
# SPDX-License-Identifier: MIT OR Apache-2.0

"""Entrypoint: `python -m paper_boxing.frontend` -> NiceGUI (uvicorn underneath)."""

import logging
import sys

from paper_boxing.frontend.app import run
from paper_boxing.frontend.config import load_config


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
    cfg = load_config()
    if not cfg.storage_secret:
        print(
            "paper-boxing-frontend: PAPER_BOXING_STORAGE_SECRET is not set; refusing to start. "
            "Set it to a long random string (it signs the per-user session cookie).",
            file=sys.stderr,
        )
        raise SystemExit(2)
    run(cfg)


if __name__ == "__main__":
    main()
