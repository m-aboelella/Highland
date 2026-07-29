from __future__ import annotations

import json
from collections.abc import Mapping
from contextlib import AsyncExitStack
from datetime import timedelta
from typing import Any, Protocol

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

INDEXABLE_SOURCES = ("archive", "relay", "beacon", "pulse", "track")
CONNECTOR_FOR_SOURCE = {
    "archive": "knowledge",
    "relay": "support",
    "beacon": "observability",
    "pulse": "communications",
    "track": "projects",
}
LIST_TOOLS_FOR_SOURCE = {
    "archive": ("list_documents",),
    "relay": ("list_customer_tickets",),
    "beacon": ("list_incidents",),
    "pulse": ("list_messages", "list_customer_meetings"),
    "track": ("list_project_issues",),
}


class SourceReadError(RuntimeError):
    def __init__(self, source: str, message: str) -> None:
        super().__init__(f"{source}: {message}")
        self.source = source


class SourceReader(Protocol):
    async def list_records(self, source: str) -> list[dict[str, Any]]: ...


class MCPSourceReader:
    """Small ingestion-only MCP client; runtime supervision belongs to E3.1."""

    def __init__(
        self,
        connector_commands: Mapping[str, tuple[str, ...]],
        *,
        timeout_seconds: float = 15,
    ) -> None:
        self.connector_commands = connector_commands
        self.timeout = timedelta(seconds=timeout_seconds)

    async def list_records(self, source: str) -> list[dict[str, Any]]:
        connector = CONNECTOR_FOR_SOURCE[source]
        command = self.connector_commands.get(connector)
        if not command:
            raise SourceReadError(source, f"connector {connector!r} is not configured")
        parameters = StdioServerParameters(command=command[0], args=list(command[1:]))
        try:
            async with AsyncExitStack() as stack:
                read, write = await stack.enter_async_context(stdio_client(parameters))
                session = await stack.enter_async_context(
                    ClientSession(read, write, read_timeout_seconds=self.timeout)
                )
                await session.initialize()
                records: list[dict[str, Any]] = []
                for tool_name in LIST_TOOLS_FOR_SOURCE[source]:
                    result = await session.call_tool(tool_name, {})
                    if result.isError:
                        raise SourceReadError(source, _result_text(result))
                    payload = result.structuredContent
                    if not isinstance(payload, dict):
                        payload = json.loads(_result_text(result))
                    items = payload.get("items")
                    if not isinstance(items, list):
                        raise SourceReadError(
                            source, "connector response did not contain an item list"
                        )
                    records.extend(dict(item) for item in items if isinstance(item, dict))
                return records
        except SourceReadError:
            raise
        except Exception as error:
            raise SourceReadError(source, str(error)) from error


def _result_text(result: Any) -> str:
    return "\n".join(
        str(getattr(block, "text", "")) for block in result.content if getattr(block, "text", None)
    )
