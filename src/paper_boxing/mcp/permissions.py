# SPDX-FileCopyrightText: 2026 Gary Frattarola <garyf@parkviewlab.ai>
#
# SPDX-License-Identifier: MIT OR Apache-2.0

"""Permission tiers for the MCP tool surface.

The scope is a property of the confirmed agent token (docs/design.md section
4a), not of the server, so there is no server-wide scope variable. The tiers
themselves are the contract's, shared with the backend's token scopes:
`paper_boxing.common.scopes`. This module re-exports them under the handbook's
module name so the MCP subpackage keeps the standard layout.
"""

from paper_boxing.common.scopes import SCOPE_TIER, Scope, scope_allows

__all__ = ["SCOPE_TIER", "Scope", "scope_allows"]
