from __future__ import annotations

from typing import Annotated, Any

from fastapi import FastAPI, Header, HTTPException
from fastapi.responses import JSONResponse
from mcp.server.fastmcp import FastMCP

from .common import (
    SourceClient,
    TicketCreate,
    filtered,
    find,
    make_record,
    utc_now,
)

META = {"dataset_version": "2026.07.29.1", "as_of": "2026-07-29T12:00:00Z"}


def seed_fragment() -> dict[str, Any]:
    tickets = [
        make_record(
            "relay",
            "tkt_1042",
            id="tkt_1042",
            key="SUP-1042",
            customer_id="cus_northwind",
            title="Production search latency above SLO",
            status="open",
            priority="P1",
            category="performance",
            assignee={"id": "usr_priya", "name": "Priya Nair"},
            requester={"id": "con_tomas", "name": "Tomas Reed"},
            created_at="2026-07-28T13:18:00Z",
            updated_at="2026-07-29T11:42:00Z",
            description=(
                "Northwind reports analyst searches taking 1.5–2.2 seconds. Normal p95 is "
                "under 700 ms. The slowdown began after the weekend maintenance window."
            ),
            comments=[
                {
                    "id": "cmt_1042_1",
                    "author": "Tomas Reed",
                    "visibility": "external",
                    "created_at": "2026-07-28T13:18:00Z",
                    "text": "The issue is reproducible across policy and analyst indexes.",
                },
                {
                    "id": "cmt_1042_2",
                    "author": "Priya Nair",
                    "visibility": "internal",
                    "created_at": "2026-07-29T08:05:00Z",
                    "text": (
                        "Query volume is flat. Beacon shows retrieval shard 3 memory at 91% "
                        "during compaction. Pausing the job brought p95 below 900 ms."
                    ),
                },
                {
                    "id": "cmt_1042_3",
                    "author": "Jonah Okafor",
                    "visibility": "internal",
                    "created_at": "2026-07-29T11:42:00Z",
                    "text": (
                        "Hypothesis is memory contention from the first 4.18.2 compaction, "
                        "not yet confirmed. Need a controlled compaction tonight."
                    ),
                },
            ],
            visibility="support",
            source_url="https://relay.summit.test/tickets/SUP-1042",
        ),
        make_record(
            "relay",
            "tkt_1033",
            id="tkt_1033",
            key="SUP-1033",
            customer_id="cus_northwind",
            title="Collector certificate rotation",
            status="resolved",
            priority="P3",
            category="configuration",
            assignee={"id": "usr_priya", "name": "Priya Nair"},
            requester={"id": "con_tomas", "name": "Tomas Reed"},
            created_at="2026-07-16T09:20:00Z",
            updated_at="2026-07-18T16:12:00Z",
            description="Beacon telemetry collector certificate was due to expire.",
            comments=[],
            resolution="New certificate installed and telemetry continuity verified.",
            visibility="support",
            source_url="https://relay.summit.test/tickets/SUP-1033",
        ),
        make_record(
            "relay",
            "tkt_1018",
            id="tkt_1018",
            key="SUP-1018",
            customer_id="cus_northwind",
            title="Bulk ingestion job missed two PDFs",
            status="resolved",
            priority="P3",
            category="ingestion",
            assignee={"id": "usr_sam", "name": "Sam Rivera"},
            requester={"id": "con_tomas", "name": "Tomas Reed"},
            created_at="2026-06-20T10:00:00Z",
            updated_at="2026-06-21T12:00:00Z",
            description="Two password-protected PDFs were skipped by the ingestion job.",
            comments=[],
            resolution="Files were re-exported without passwords and ingested.",
            visibility="support",
            source_url="https://relay.summit.test/tickets/SUP-1018",
        ),
        make_record(
            "relay",
            "tkt_1045",
            id="tkt_1045",
            key="SUP-1045",
            customer_id="cus_alpine",
            title="Synonym export format question",
            status="open",
            priority="P4",
            category="how-to",
            assignee={"id": "usr_sam", "name": "Sam Rivera"},
            requester={"id": "con_alpine_ops", "name": "Riley Morgan"},
            created_at="2026-07-29T08:00:00Z",
            updated_at="2026-07-29T08:00:00Z",
            description="Customer asks whether synonym exports preserve comments.",
            comments=[],
            visibility="support",
            source_url="https://relay.summit.test/tickets/SUP-1045",
        ),
    ]

    return {"meta": META, "tickets": tickets, "idempotency": {}}


def register_routes(app: FastAPI) -> None:
    @app.get("/tickets")
    def list_tickets(
        customer_id: str | None = None,
        status: str | None = None,
        priority: str | None = None,
    ) -> dict[str, Any]:
        records = filtered(
            app.state.store.read()["tickets"], customer_id=customer_id, status=status
        )
        if priority is not None:
            records = [record for record in records if record["priority"] == priority]
        return {"items": records, "count": len(records)}

    @app.get("/tickets/{ticket_id}")
    def get_ticket(ticket_id: str) -> dict[str, Any]:
        record = find(app.state.store.read()["tickets"], ticket_id)
        if record is None:
            raise HTTPException(status_code=404, detail="Ticket not found")
        return record

    @app.post("/tickets", status_code=201)
    def create_ticket(
        request: TicketCreate,
        idempotency_key: Annotated[str | None, Header(alias="Idempotency-Key")] = None,
    ) -> JSONResponse:
        if not idempotency_key:
            raise HTTPException(status_code=400, detail="Idempotency-Key header is required")
        data = app.state.store.read()
        existing_id = data["idempotency"].get(idempotency_key)
        if existing_id:
            existing = find(data["tickets"], existing_id)
            return JSONResponse(status_code=200, content=existing)
        next_number = (
            max([int(record["key"].split("-")[1]) for record in data["tickets"]] + [1000]) + 1
        )
        ticket_id = f"tkt_{next_number}"
        now = utc_now()
        record = make_record(
            "relay",
            ticket_id,
            id=ticket_id,
            key=f"SUP-{next_number}",
            customer_id=request.customer_id,
            title=request.title,
            status="open",
            priority=request.priority,
            category=request.category,
            assignee=None,
            requester={"id": "highland-agent", "name": "Highland Agent"},
            created_at=now,
            updated_at=now,
            description=request.description,
            comments=[],
            visibility="support",
            source_url=f"https://relay.summit.test/tickets/SUP-{next_number}",
        )
        data["tickets"].append(record)
        data["idempotency"][idempotency_key] = ticket_id
        app.state.store.write(data)
        return JSONResponse(status_code=201, content=record)


def register_tools(mcp: FastMCP, client: SourceClient) -> None:

    @mcp.tool()
    def list_customer_tickets(
        customer_id: str | None = None,
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
