from __future__ import annotations

import argparse
import os
from typing import Any

import httpx
from mcp.server.fastmcp import FastMCP

from .constants import DEFAULT_PORTS, SERVICE_PRODUCTS, SERVICES


def _base_url(connector: str) -> str:
    variable = f"HIGHLAND_{connector.upper()}_URL"
    return os.getenv(variable, f"http://localhost:{DEFAULT_PORTS[connector]}").rstrip("/")


class SourceClient:
    def __init__(self, connector: str) -> None:
        self.connector = connector
        self.base_url = _base_url(connector)
        self.client = httpx.Client(base_url=self.base_url, timeout=10)

    def get(self, path: str, params: dict[str, Any] | None = None) -> Any:
        try:
            response = self.client.get(
                path,
                params={key: value for key, value in (params or {}).items() if value is not None},
            )
            response.raise_for_status()
            return response.json()
        except httpx.HTTPStatusError as error:
            raise RuntimeError(
                f"{SERVICE_PRODUCTS[self.connector]} returned HTTP "
                f"{error.response.status_code}: {_error_text(error.response)}"
            ) from None
        except httpx.RequestError as error:
            raise RuntimeError(
                f"{SERVICE_PRODUCTS[self.connector]} is unavailable at {self.base_url}: {error}"
            ) from None

    def post(
        self,
        path: str,
        body: dict[str, Any],
        *,
        idempotency_key: str | None = None,
    ) -> Any:
        headers = {"Idempotency-Key": idempotency_key} if idempotency_key else {}
        try:
            response = self.client.post(path, json=body, headers=headers)
            response.raise_for_status()
            return response.json()
        except httpx.HTTPStatusError as error:
            raise RuntimeError(
                f"{SERVICE_PRODUCTS[self.connector]} returned HTTP "
                f"{error.response.status_code}: {_error_text(error.response)}"
            ) from None
        except httpx.RequestError as error:
            raise RuntimeError(
                f"{SERVICE_PRODUCTS[self.connector]} is unavailable at {self.base_url}: {error}"
            ) from None


def _error_text(response: httpx.Response) -> str:
    try:
        body = response.json()
        return str(body.get("detail") or body.get("message") or body.get("error"))
    except ValueError:
        return response.text[:200]


def build_mcp(connector: str) -> FastMCP:
    if connector not in SERVICES:
        raise ValueError(f"Unknown connector: {connector}")
    product = SERVICE_PRODUCTS[connector]
    mcp = FastMCP(
        name=f"Highland — {product}",
        instructions=(
            f"Atomic tools for the synthetic {product} service. All records are fictional. "
            "Preserve record IDs and source URLs in downstream evidence."
        ),
    )
    client = SourceClient(connector)

    if connector == "crm":

        @mcp.tool()
        def list_customers(
            status: str | None = None,
            health: str | None = None,
            region: str | None = None,
        ) -> dict[str, Any]:
            """List CRM customer accounts, optionally filtering status, health, or region."""
            return client.get("/customers", {"status": status, "health": health, "region": region})

        @mcp.tool()
        def get_customer(customer_id: str) -> dict[str, Any]:
            """Get one CRM customer by stable ID, such as cus_northwind."""
            return client.get(f"/customers/{customer_id}")

    elif connector == "knowledge":

        @mcp.tool()
        def search_documents(
            query: str,
            customer_id: str | None = None,
            document_type: str | None = None,
            team: str | None = None,
            limit: int = 10,
        ) -> dict[str, Any]:
            """Search exact passages in product docs, runbooks, policies, and customer plans."""
            return client.get(
                "/search",
                {
                    "query": query,
                    "customer_id": customer_id,
                    "document_type": document_type,
                    "team": team,
                    "limit": limit,
                },
            )

        @mcp.tool()
        def get_document(document_id: str) -> dict[str, Any]:
            """Get a complete document and all of its citable passages."""
            return client.get(f"/documents/{document_id}")

    elif connector == "support":

        @mcp.tool()
        def list_customer_tickets(
            customer_id: str,
            status: str | None = None,
            priority: str | None = None,
        ) -> dict[str, Any]:
            """List support tickets for one customer."""
            return client.get(
                "/tickets",
                {"customer_id": customer_id, "status": status, "priority": priority},
            )

        @mcp.tool()
        def get_ticket(ticket_id: str) -> dict[str, Any]:
            """Get a support ticket by ID or key, including comments."""
            return client.get(f"/tickets/{ticket_id}")

        @mcp.tool()
        def create_ticket(
            customer_id: str,
            title: str,
            description: str,
            idempotency_key: str,
            priority: str = "P3",
            category: str = "investigation",
        ) -> dict[str, Any]:
            """Create a support ticket. Highland policy requires human approval before use."""
            return client.post(
                "/tickets",
                {
                    "customer_id": customer_id,
                    "title": title,
                    "description": description,
                    "priority": priority,
                    "category": category,
                },
                idempotency_key=idempotency_key,
            )

    elif connector == "observability":

        @mcp.tool()
        def get_deployment(customer_id: str) -> dict[str, Any]:
            """Get deployment topology, version, and health for a customer."""
            result = client.get("/deployments", {"customer_id": customer_id})
            if result["count"] == 0:
                raise RuntimeError(f"No deployment found for customer {customer_id}")
            return result

        @mcp.tool()
        def list_available_metrics(customer_id: str) -> dict[str, Any]:
            """List metric names available for a customer before querying values."""
            return client.get("/metrics", {"customer_id": customer_id})

        @mcp.tool()
        def query_deployment_metrics(
            customer_id: str,
            metric: str,
            time_range: str = "24h",
        ) -> dict[str, Any]:
            """Query a customer metric over 1h, 6h, 12h, 24h, 48h, or 7d."""
            return client.post(
                "/metrics/query",
                {
                    "customer_id": customer_id,
                    "metric": metric,
                    "time_range": time_range,
                },
            )

        @mcp.tool()
        def list_incidents(
            customer_id: str,
            status: str | None = None,
        ) -> dict[str, Any]:
            """List operational incidents for a customer."""
            return client.get("/incidents", {"customer_id": customer_id, "status": status})

    elif connector == "communications":

        @mcp.tool()
        def search_messages(
            query: str,
            customer_id: str | None = None,
            since: str | None = None,
            limit: int = 10,
        ) -> dict[str, Any]:
            """Search internal messages, preserving channel and visibility metadata."""
            return client.get(
                "/messages/search",
                {
                    "query": query,
                    "customer_id": customer_id,
                    "since": since,
                    "limit": limit,
                },
            )

        @mcp.tool()
        def list_customer_meetings(
            customer_id: str,
            starts_after: str | None = None,
            starts_before: str | None = None,
        ) -> dict[str, Any]:
            """List past or upcoming customer meetings and notes."""
            return client.get(
                "/meetings",
                {
                    "customer_id": customer_id,
                    "starts_after": starts_after,
                    "starts_before": starts_before,
                },
            )

        @mcp.tool()
        def post_customer_update(
            customer_id: str,
            message: str,
            idempotency_key: str,
            channel: str = "customer-operations",
        ) -> dict[str, Any]:
            """Post an external customer update. Highland policy requires human approval."""
            return client.post(
                "/customer-updates",
                {
                    "customer_id": customer_id,
                    "message": message,
                    "channel": channel,
                },
                idempotency_key=idempotency_key,
            )

    elif connector == "projects":

        @mcp.tool()
        def list_project_issues(
            customer_id: str | None = None,
            project_id: str | None = None,
            status: str | None = None,
        ) -> dict[str, Any]:
            """List project issues by customer, project, or status."""
            return client.get(
                "/issues",
                {
                    "customer_id": customer_id,
                    "project_id": project_id,
                    "status": status,
                },
            )

        @mcp.tool()
        def get_project_issue(issue_id: str) -> dict[str, Any]:
            """Get one project issue by stable ID or human key."""
            return client.get(f"/issues/{issue_id}")

        @mcp.tool()
        def create_project_issue(
            project_id: str,
            title: str,
            description: str,
            idempotency_key: str,
            customer_id: str | None = None,
            priority: str = "medium",
            labels: list[str] | None = None,
        ) -> dict[str, Any]:
            """Create a project issue. Highland policy requires human approval before use."""
            return client.post(
                "/issues",
                {
                    "project_id": project_id,
                    "customer_id": customer_id,
                    "title": title,
                    "description": description,
                    "priority": priority,
                    "labels": labels or [],
                },
                idempotency_key=idempotency_key,
            )

    return mcp


def main() -> None:
    parser = argparse.ArgumentParser(
        prog="highland-mcp",
        description="Run one Highland mock-system MCP connector over stdio.",
    )
    parser.add_argument("connector", choices=SERVICES)
    args = parser.parse_args()
    build_mcp(args.connector).run(transport="stdio")


if __name__ == "__main__":
    main()
