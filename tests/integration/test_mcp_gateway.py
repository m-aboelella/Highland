from __future__ import annotations

import sys

import pytest

from highland.runtime.mcp import MCPGateway

CONNECTORS = ("crm", "knowledge", "support", "observability", "communications", "projects")


def commands() -> dict[str, tuple[str, ...]]:
    return {
        name: (sys.executable, "-m", "highland_mocks.mcp_server", name)
        for name in CONNECTORS
    }


@pytest.mark.asyncio
async def test_discovers_and_qualifies_all_connector_tools() -> None:
    async with MCPGateway(commands()) as gateway:
        names = {tool.qualified_name for tool in gateway.tools}
        assert {
            "crm__get_customer",
            "knowledge__get_document",
            "support__create_ticket",
            "observability__query_deployment_metrics",
            "communications__post_customer_update",
            "projects__create_project_issue",
        } <= names
        assert {tool.connector for tool in gateway.tools} == set(CONNECTORS)
        assert all(tool.input_schema["type"] == "object" for tool in gateway.tools)
        assert {tool.name for tool in gateway.model_tools()} == names


@pytest.mark.asyncio
async def test_unavailable_connector_does_not_hide_healthy_tools() -> None:
    configured = commands()
    configured["broken"] = ("/definitely/missing/highland-mcp",)
    async with MCPGateway(configured, startup_timeout_seconds=3) as gateway:
        assert "broken" in gateway.failures
        assert "crm__list_customers" in {tool.qualified_name for tool in gateway.tools}


@pytest.mark.asyncio
async def test_unknown_tool_returns_bounded_source_aware_error() -> None:
    async with MCPGateway({"crm": commands()["crm"]}) as gateway:
        result = await gateway.call("crm__does_not_exist", {})
        assert result.is_error
        assert result.error_type == "unknown_tool"
