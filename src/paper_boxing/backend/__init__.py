# SPDX-FileCopyrightText: 2026 Gary Frattarola <garyf@parkviewlab.ai>
#
# SPDX-License-Identifier: MIT OR Apache-2.0

"""The backend: the only writer of files and of the database, and the one
implementation of every rule. Serves the REST API under /api/v1; see
docs/api.md for the contract and docs/design.md section 4 for the design."""
