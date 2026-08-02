from __future__ import annotations

from contextlib import asynccontextmanager
from datetime import timedelta
from types import SimpleNamespace
from typing import Any, Self

import pytest

from highland.retrieval import sources
from highland.retrieval.sources import MCPSourceReader, SourceReadError


@pytest.mark.asyncio
async def test_connector_inherits_service_url_environment_and_timeout(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    observed: dict[str, Any] = {}

    @asynccontextmanager
    async def fake_stdio(parameters: Any):
        observed["parameters"] = parameters
        yield object(), object()

    class FakeSession:
        def __init__(
            self,
            _read: object,
            _write: object,
            *,
            read_timeout_seconds: timedelta,
        ) -> None:
            observed["timeout"] = read_timeout_seconds

        async def __aenter__(self) -> Self:
            return self

        async def __aexit__(self, *_: object) -> None:
            return None

        async def initialize(self) -> None:
            return None

        async def call_tool(self, _name: str, _arguments: dict[str, Any]) -> Any:
            return SimpleNamespace(
                isError=False,
                structuredContent={"items": [{"id": "document-1"}]},
                content=[],
            )

    monkeypatch.setenv("HIGHLAND_KNOWLEDGE_URL", "http://archive:8102")
    monkeypatch.setattr(sources, "stdio_client", fake_stdio)
    monkeypatch.setattr(sources, "ClientSession", FakeSession)

    records = await MCPSourceReader(
        {"knowledge": ("highland-mcp", "knowledge")},
        timeout_seconds=7,
    ).list_records("archive")

    parameters = observed["parameters"]
    assert parameters.command == "highland-mcp"
    assert parameters.args == ["knowledge"]
    assert parameters.env["HIGHLAND_KNOWLEDGE_URL"] == "http://archive:8102"
    assert observed["timeout"] == timedelta(seconds=7)
    assert records == [{"id": "document-1"}]


@pytest.mark.asyncio
async def test_connector_startup_errors_remain_source_scoped(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    @asynccontextmanager
    async def failing_stdio(_parameters: Any):
        raise RuntimeError("connector startup failed")
        yield  # pragma: no cover - makes this an async context manager

    monkeypatch.setattr(sources, "stdio_client", failing_stdio)

    reader = MCPSourceReader({"support": ("highland-mcp", "support")})

    with pytest.raises(SourceReadError, match="^relay: connector startup failed$"):
        await reader.list_records("relay")
