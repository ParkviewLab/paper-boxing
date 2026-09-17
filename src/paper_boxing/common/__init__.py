# SPDX-FileCopyrightText: 2026 Gary Frattarola <garyf@parkviewlab.ai>
#
# SPDX-License-Identifier: MIT OR Apache-2.0

"""Shared by every component: the API contract (schemas, routes, client) and the
environment helpers. `common` imports none of `backend`, `frontend` or `mcp`;
tests/test_import_boundaries.py enforces that."""
