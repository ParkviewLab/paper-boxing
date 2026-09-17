# SPDX-FileCopyrightText: 2026 Gary Frattarola <garyf@parkviewlab.ai>
#
# SPDX-License-Identifier: MIT OR Apache-2.0

"""An MCP client for the tier: the SDK's own Streamable-HTTP client and
`ClientSession` over the published port, the way `claude mcp add --transport
http` connects, with the agent token in the `Authorization` header of every
request.

The server is stateless, so a session is the client's notion only: `Agent.run`
opens one (the `initialize` handshake, then whatever the test does with the
session) and closes it. `call` reads a tool's result from `structuredContent`;
a refusal (`isError`) is raised as `ToolRefused`, carrying the contract's error
code and message.
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any

import httpx
from mcp import ClientSession, types
from mcp.client.streamable_http import streamable_http_client


class ToolRefused(Exception):
    """A tool result with `isError`: the contract's error body, as the agent reads it."""

    def __init__(self, name: str, body: dict[str, Any]) -> None:
        self.name = name
        self.code: str = body["error"]["code"]
        self.message: str = body["error"]["message"]
        super().__init__(f"{name}: {self.code}: {self.message}")


@dataclass(frozen=True)
class Agent:
    """One agent: the MCP endpoint and the bearer it holds."""

    url: str
    token: str

    def run[T](self, work: Callable[[ClientSession], Awaitable[T]]) -> T:
        """Open a session, run `work` against it, return its result.

        A `ToolRefused` is caught inside the session and raised again here: the SDK's session
        runs a task group, which would otherwise wrap it in an `ExceptionGroup` on the way out.
        """

        async def go() -> T | ToolRefused:
            headers = {"Authorization": f"Bearer {self.token}"}
            async with (
                httpx.AsyncClient(headers=headers, timeout=httpx.Timeout(30.0, read=300.0)) as http,
                streamable_http_client(self.url, http_client=http) as (read, write, _),
                ClientSession(read, write) as session,
            ):
                await session.initialize()
                try:
                    return await work(session)
                except ToolRefused as refused:
                    return refused

        outcome = asyncio.run(go())
        if isinstance(outcome, ToolRefused):
            raise outcome
        return outcome

    def tool_names(self) -> list[str]:
        async def work(session: ClientSession) -> list[str]:
            return [tool.name for tool in (await session.list_tools()).tools]

        return self.run(work)

    def call(self, name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        """One tool call in a session of its own."""
        return self.run(lambda session: call(session, name, arguments))


async def call(session: ClientSession, name: str, arguments: dict[str, Any]) -> dict[str, Any]:
    """Call a tool; return its structured output, or raise `ToolRefused` for an `isError` result."""
    result = await session.call_tool(name, arguments)
    if result.isError:
        first = result.content[0] if result.content else None
        text = first.text if isinstance(first, types.TextContent) else "{}"
        raise ToolRefused(name, json.loads(text))
    assert result.structuredContent is not None, result
    return result.structuredContent
