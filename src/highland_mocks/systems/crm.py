from __future__ import annotations

from typing import Any

from fastapi import FastAPI, HTTPException
from mcp.server.fastmcp import FastMCP

from .common import (
    SourceClient,
    find,
    make_record,
)

META = {"dataset_version": "2026.07.29.1", "as_of": "2026-07-29T12:00:00Z"}


def seed_fragment() -> dict[str, Any]:
    customers = [
        make_record(
            "atlas",
            "cus_northwind",
            id="cus_northwind",
            name="Northwind Bank",
            industry="Financial services",
            tier="Strategic",
            status="active",
            region="EU",
            account_owner={"id": "usr_maya", "name": "Maya Chen"},
            technical_owner={"id": "usr_jonah", "name": "Jonah Okafor"},
            renewal_date="2026-10-31",
            annual_contract_value_usd=780000,
            health="watch",
            health_reason="Open P1 retrieval-latency issue and renewal in 94 days",
            deployment_ids=["dep_northwind_prod"],
            contacts=[
                {
                    "id": "con_elena",
                    "name": "Elena Varga",
                    "role": "VP, Digital Platforms",
                    "email": "elena.varga@northwind-bank.test",
                    "decision_role": "executive sponsor",
                },
                {
                    "id": "con_tomas",
                    "name": "Tomas Reed",
                    "role": "Search Platform Lead",
                    "email": "tomas.reed@northwind-bank.test",
                    "decision_role": "technical owner",
                },
            ],
            tags=["regulated", "private-cloud", "renewal-q4"],
            next_meeting_at="2026-07-30T09:30:00Z",
            notes=(
                "Northwind uses Summit Search for internal policy and analyst research. "
                "They value predictable latency and auditability over raw throughput."
            ),
            visibility="customer-success",
            updated_at="2026-07-29T09:10:00Z",
            source_url="https://atlas.summit.test/customers/cus_northwind",
        ),
        make_record(
            "atlas",
            "cus_alpine",
            id="cus_alpine",
            name="Alpine Outfitters",
            industry="Retail",
            tier="Enterprise",
            status="active",
            region="North America",
            account_owner={"id": "usr_maya", "name": "Maya Chen"},
            technical_owner={"id": "usr_priya", "name": "Priya Nair"},
            renewal_date="2027-02-28",
            annual_contract_value_usd=240000,
            health="healthy",
            health_reason="Stable deployment and expanding usage",
            deployment_ids=["dep_alpine_prod"],
            contacts=[],
            tags=["cloud", "expansion"],
            next_meeting_at="2026-08-05T16:00:00Z",
            notes="Alpine powers product-support search for store associates.",
            visibility="customer-success",
            updated_at="2026-07-28T15:00:00Z",
            source_url="https://atlas.summit.test/customers/cus_alpine",
        ),
        make_record(
            "atlas",
            "cus_lumon",
            id="cus_lumon",
            name="Lumon Health",
            industry="Healthcare",
            tier="Enterprise",
            status="active",
            region="North America",
            account_owner={"id": "usr_omar", "name": "Omar Haddad"},
            technical_owner={"id": "usr_jonah", "name": "Jonah Okafor"},
            renewal_date="2027-01-15",
            annual_contract_value_usd=320000,
            health="at_risk",
            health_reason="Security review is blocking the production rollout",
            deployment_ids=["dep_lumon_stage"],
            contacts=[],
            tags=["regulated", "healthcare", "pre-production"],
            next_meeting_at=None,
            notes="Lumon is still in staging pending a security exception.",
            visibility="customer-success",
            updated_at="2026-07-27T18:30:00Z",
            source_url="https://atlas.summit.test/customers/cus_lumon",
        ),
    ]

    return {"meta": META, "customers": customers}


def register_routes(app: FastAPI) -> None:
    @app.get("/customers")
    def list_customers(
        status: str | None = None,
        health: str | None = None,
        region: str | None = None,
    ) -> dict[str, Any]:
        records = app.state.store.read()["customers"]
        results = [
            record
            for record in records
            if (status is None or record["status"] == status)
            and (health is None or record["health"] == health)
            and (region is None or record["region"].lower() == region.lower())
        ]
        return {"items": results, "count": len(results)}

    @app.get("/customers/{customer_id}")
    def get_customer(customer_id: str) -> dict[str, Any]:
        record = find(app.state.store.read()["customers"], customer_id)
        if record is None:
            raise HTTPException(status_code=404, detail="Customer not found")
        return record


def register_tools(mcp: FastMCP, client: SourceClient) -> None:

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
