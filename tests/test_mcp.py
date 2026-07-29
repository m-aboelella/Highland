from __future__ import annotations

import pytest

from highland_mocks.mcp_server import build_mcp


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("connector", "expected"),
    [
        ("crm", {"list_customers", "get_customer"}),
        ("knowledge", {"list_documents", "search_documents", "get_document"}),
        (
            "support",
            {"list_customer_tickets", "get_ticket", "create_ticket"},
        ),
        (
            "observability",
            {
                "get_deployment",
                "list_available_metrics",
                "query_deployment_metrics",
                "list_incidents",
            },
        ),
        (
            "communications",
            {
                "search_messages",
                "list_messages",
                "list_customer_meetings",
                "post_customer_update",
            },
        ),
        (
            "projects",
            {"list_project_issues", "get_project_issue", "create_project_issue"},
        ),
    ],
)
async def test_connector_exposes_only_its_atomic_tools(connector: str, expected: set[str]) -> None:
    tools = await build_mcp(connector).list_tools()
    assert {tool.name for tool in tools} == expected
