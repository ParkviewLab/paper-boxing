# SPDX-FileCopyrightText: 2026 Gary Frattarola <garyf@parkviewlab.ai>
#
# SPDX-License-Identifier: MIT OR Apache-2.0

"""The MCP server: the interface for agents. A standard ParkviewLab MCP server
(Streamable HTTP at /mcp, POST only) that calls the backend's REST API for
everything through `paper_boxing.common.client.BackendClient`."""
