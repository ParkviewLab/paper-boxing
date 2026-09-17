# SPDX-FileCopyrightText: 2026 Gary Frattarola <garyf@parkviewlab.ai>
#
# SPDX-License-Identifier: MIT OR Apache-2.0

# One Dockerfile, three images (docs/architecture.md, "The package and the
# images"): a shared base with the uv python3.13 slim image and the project
# files, then one target per component. Each target installs only its own
# extra, exposes its own port, checks its own /health, and runs
# `python -m paper_boxing.<component>`.
#
#   docker build --target backend  -t paper-boxing-backend  .
#   docker build --target frontend -t paper-boxing-frontend .
#   docker build --target mcp      -t paper-boxing-mcp      .

FROM ghcr.io/astral-sh/uv:python3.13-bookworm-slim AS base

WORKDIR /app

ENV PYTHONUNBUFFERED=1 \
    HOST=0.0.0.0

# Dependency manifests first, so the dependency layer caches across source changes.
COPY pyproject.toml uv.lock ./

# ---------------------------------------------------------------------------
FROM base AS backend

RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --locked --no-dev --no-install-project --extra backend

# README.md and the licence files are package metadata (pyproject.toml -> readme,
# license-files); the second sync installs the project itself and reads them.
COPY README.md LICENSE-MIT LICENSE-APACHE ./
COPY src/ src/
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --locked --no-dev --extra backend

ENV PORT=35843 \
    PAPER_BOXING_DATA_DIR=/data

EXPOSE 35843
VOLUME ["/data"]
HEALTHCHECK --interval=15s --timeout=3s --start-period=10s --retries=3 \
    CMD ["python", "-c", "import urllib.request; urllib.request.urlopen('http://127.0.0.1:35843/health', timeout=2)"]

CMD ["uv", "run", "--no-sync", "python", "-m", "paper_boxing.backend"]

# ---------------------------------------------------------------------------
FROM base AS frontend

RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --locked --no-dev --no-install-project --extra frontend

COPY README.md LICENSE-MIT LICENSE-APACHE ./
COPY src/ src/
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --locked --no-dev --extra frontend

ENV PORT=35840

EXPOSE 35840
HEALTHCHECK --interval=15s --timeout=3s --start-period=15s --retries=3 \
    CMD ["python", "-c", "import urllib.request; urllib.request.urlopen('http://127.0.0.1:35840/health', timeout=2)"]

CMD ["uv", "run", "--no-sync", "python", "-m", "paper_boxing.frontend"]

# ---------------------------------------------------------------------------
FROM base AS mcp

RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --locked --no-dev --no-install-project --extra mcp

COPY README.md LICENSE-MIT LICENSE-APACHE ./
COPY src/ src/
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --locked --no-dev --extra mcp

ENV PORT=35842

EXPOSE 35842
HEALTHCHECK --interval=15s --timeout=3s --start-period=10s --retries=3 \
    CMD ["python", "-c", "import urllib.request; urllib.request.urlopen('http://127.0.0.1:35842/health', timeout=2)"]

CMD ["uv", "run", "--no-sync", "python", "-m", "paper_boxing.mcp"]
