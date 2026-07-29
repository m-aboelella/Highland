from __future__ import annotations

import asyncio
import json
from collections.abc import Mapping
from contextlib import AsyncExitStack
from dataclasses import dataclass
from datetime import timedelta
from typing import Any, Self

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

from highland.models.contracts import ToolDefinition


@dataclass(frozen=True, slots=True)
class MCPTool:
    connector: str
    source_name: str
    qualified_name: str
    description: str
    input_schema: dict[str, Any]

    def model_definition(self) -> ToolDefinition:
        return ToolDefinition(
            name=self.qualified_name,
            description=f"[{self.connector}] {self.description}".strip(),
            input_schema=self.input_schema,
        )


@dataclass(frozen=True, slots=True)
class NormalizedToolResult:
    connector: str
    tool: str
    content: str
    structured_content: dict[str, Any] | None = None
    is_error: bool = False
    error_type: str | None = None


@dataclass(slots=True)
class _Connection:
    stack: AsyncExitStack
    session: ClientSession


class MCPGateway:
    """Supervise configured stdio MCP connectors behind qualified tool names."""

    def __init__(
        self,
        connector_commands: Mapping[str, tuple[str, ...]],
        *,
        startup_timeout_seconds: float = 10,
        request_timeout_seconds: float = 15,
        shutdown_timeout_seconds: float = 5,
    ) -> None:
        self.connector_commands = dict(connector_commands)
        self.startup_timeout_seconds = startup_timeout_seconds
        self.request_timeout_seconds = request_timeout_seconds
        self.shutdown_timeout_seconds = shutdown_timeout_seconds
        self._connections: dict[str, _Connection] = {}
        self._tools: dict[str, MCPTool] = {}
        self.failures: dict[str, str] = {}

    async def __aenter__(self) -> Self:
        await self.start()
        return self

    async def __aexit__(self, *_: object) -> None:
        await self.close()

    @staticmethod
    def qualify(connector: str, tool: str) -> str:
        return f"{connector}__{tool}"

    async def start(self) -> list[MCPTool]:
        await asyncio.gather(
            *(self._connect(name, command) for name, command in self.connector_commands.items())
        )
        return self.tools

    async def _connect(self, connector: str, command: tuple[str, ...]) -> None:
        if not command:
            self.failures[connector] = "empty connector command"
            return
        stack = AsyncExitStack()
        try:
            async with asyncio.timeout(self.startup_timeout_seconds):
                parameters = StdioServerParameters(command=command[0], args=list(command[1:]))
                read, write = await stack.enter_async_context(stdio_client(parameters))
                session = await stack.enter_async_context(
                    ClientSession(
                        read,
                        write,
                        read_timeout_seconds=timedelta(seconds=self.request_timeout_seconds),
                    )
                )
                await session.initialize()
                response = await session.list_tools()
            self._connections[connector] = _Connection(stack=stack, session=session)
            for item in response.tools:
                qualified = self.qualify(connector, item.name)
                if qualified in self._tools:
                    raise RuntimeError(f"duplicate qualified MCP tool {qualified}")
                self._tools[qualified] = MCPTool(
                    connector=connector,
                    source_name=item.name,
                    qualified_name=qualified,
                    description=item.description or "",
                    input_schema=dict(item.inputSchema),
                )
        except BaseException as error:  # noqa: BLE001 - isolate connector startup failure
            self.failures[connector] = f"{type(error).__name__}: {error}"
            await stack.aclose()

    @property
    def tools(self) -> list[MCPTool]:
        return sorted(self._tools.values(), key=lambda tool: tool.qualified_name)

    def model_tools(self) -> list[ToolDefinition]:
        return [tool.model_definition() for tool in self.tools]

    async def call(self, qualified_name: str, arguments: Mapping[str, Any]) -> NormalizedToolResult:
        tool = self._tools.get(qualified_name)
        if tool is None:
            return NormalizedToolResult(
                connector="unknown",
                tool=qualified_name,
                content=f"Unknown or unavailable MCP tool: {qualified_name}",
                is_error=True,
                error_type="unknown_tool",
            )
        connection = self._connections[tool.connector]
        try:
            async with asyncio.timeout(self.request_timeout_seconds):
                result = await connection.session.call_tool(tool.source_name, dict(arguments))
            text = "\n".join(
                str(getattr(block, "text", ""))
                for block in result.content
                if getattr(block, "text", None)
            )
            structured = (
                dict(result.structuredContent)
                if isinstance(result.structuredContent, dict)
                else None
            )
            if not text and structured is not None:
                text = json.dumps(structured, sort_keys=True)
            return NormalizedToolResult(
                connector=tool.connector,
                tool=qualified_name,
                content=text,
                structured_content=structured,
                is_error=bool(result.isError),
                error_type="source_error" if result.isError else None,
            )
        except Exception as error:  # noqa: BLE001 - normalize transport/provider failures
            return NormalizedToolResult(
                connector=tool.connector,
                tool=qualified_name,
                content=f"{tool.connector} failed while calling {tool.source_name}: {error}",
                is_error=True,
                error_type=type(error).__name__,
            )

    async def close(self) -> None:
        connections = list(self._connections.values())
        self._connections.clear()
        self._tools.clear()
        for connection in reversed(connections):
            try:
                async with asyncio.timeout(self.shutdown_timeout_seconds):
                    await connection.stack.aclose()
            except (TimeoutError, asyncio.CancelledError, RuntimeError):
                pass
