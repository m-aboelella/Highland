from __future__ import annotations

from datetime import datetime
from typing import Annotated, Any

from fastapi import FastAPI, Header, HTTPException, Query
from fastapi.responses import JSONResponse
from mcp.server.fastmcp import FastMCP

from .common import (
    CustomerUpdateCreate,
    SourceClient,
    filtered,
    find,
    make_record,
    relevance_score,
    utc_now,
)

META = {"dataset_version": "2026.07.29.1", "as_of": "2026-07-29T12:00:00Z"}


def seed_fragment() -> dict[str, Any]:
    messages = [
        make_record(
            "pulse",
            "msg_7001",
            id="msg_7001",
            customer_id="cus_northwind",
            channel="support-northwind",
            thread_id="thr_latency_northwind",
            author={"id": "usr_priya", "name": "Priya Nair"},
            created_at="2026-07-28T14:05:00Z",
            text=(
                "I reproduced the latency. Traffic is normal, but shard 3 memory climbs while "
                "compaction runs. I have paused it and linked Beacon incident INC-208."
            ),
            visibility="support",
            updated_at="2026-07-28T14:05:00Z",
            source_url="https://pulse.summit.test/channels/support-northwind/msg_7001",
        ),
        make_record(
            "pulse",
            "msg_7002",
            id="msg_7002",
            customer_id="cus_northwind",
            channel="support-northwind",
            thread_id="thr_latency_northwind",
            author={"id": "usr_jonah", "name": "Jonah Okafor"},
            created_at="2026-07-28T14:22:00Z",
            text=(
                "This resembles the 4.18.2 first-compaction limitation, but we should call it "
                "a hypothesis until tonight's controlled run. Please avoid telling Northwind "
                "that the upgrade is definitively the root cause."
            ),
            visibility="support",
            updated_at="2026-07-28T14:22:00Z",
            source_url="https://pulse.summit.test/channels/support-northwind/msg_7002",
        ),
        make_record(
            "pulse",
            "msg_7003",
            id="msg_7003",
            customer_id="cus_northwind",
            channel="customer-success",
            thread_id="thr_northwind_meeting",
            author={"id": "usr_maya", "name": "Maya Chen"},
            created_at="2026-07-29T09:02:00Z",
            text=(
                "Tomorrow Elena will want to know whether analysts are still affected, what "
                "we know versus suspect, and whether the October renewal timeline is at risk. "
                "We also owe Tomas a date for the capacity review."
            ),
            visibility="customer-success",
            updated_at="2026-07-29T09:02:00Z",
            source_url="https://pulse.summit.test/channels/customer-success/msg_7003",
        ),
        make_record(
            "pulse",
            "msg_7004",
            id="msg_7004",
            customer_id="cus_northwind",
            channel="engineering-search",
            thread_id="thr_latency_northwind",
            author={"id": "usr_lee", "name": "Lee Park"},
            created_at="2026-07-29T10:40:00Z",
            text=(
                "Patch 4.18.3 can cap compaction memory, but it has not passed private-cloud "
                "qualification. The safe near-term mitigation is concurrency one plus a "
                "capacity review."
            ),
            visibility="engineering",
            updated_at="2026-07-29T10:40:00Z",
            source_url="https://pulse.summit.test/channels/engineering-search/msg_7004",
        ),
        make_record(
            "pulse",
            "msg_7010",
            id="msg_7010",
            customer_id="cus_alpine",
            channel="customer-success",
            thread_id="thr_alpine_expansion",
            author={"id": "usr_maya", "name": "Maya Chen"},
            created_at="2026-07-28T17:00:00Z",
            text="Alpine wants to add two regions next quarter; current search health is strong.",
            visibility="customer-success",
            updated_at="2026-07-28T17:00:00Z",
            source_url="https://pulse.summit.test/channels/customer-success/msg_7010",
        ),
    ]

    meetings = [
        make_record(
            "pulse",
            "mtg_northwind_0730",
            id="mtg_northwind_0730",
            customer_id="cus_northwind",
            title="Northwind weekly deployment review",
            starts_at="2026-07-30T09:30:00Z",
            duration_minutes=45,
            attendees=[
                {"name": "Maya Chen", "organization": "Summit Software"},
                {"name": "Jonah Okafor", "organization": "Summit Software"},
                {"name": "Elena Varga", "organization": "Northwind Bank"},
                {"name": "Tomas Reed", "organization": "Northwind Bank"},
            ],
            agenda=[
                "Production service health",
                "Latency incident and mitigation",
                "Capacity review",
                "Q4 ingestion forecast and renewal readiness",
            ],
            notes=None,
            visibility="customer-success",
            updated_at="2026-07-29T09:00:00Z",
            source_url="https://pulse.summit.test/meetings/mtg_northwind_0730",
        ),
        make_record(
            "pulse",
            "mtg_northwind_0723",
            id="mtg_northwind_0723",
            customer_id="cus_northwind",
            title="Northwind weekly deployment review",
            starts_at="2026-07-23T09:30:00Z",
            duration_minutes=45,
            attendees=[],
            agenda=[],
            notes=(
                "Northwind approved the 4.18.2 maintenance window. Tomas asked Summit to "
                "quantify headroom before Q4. Elena reiterated that stable response time is "
                "a renewal requirement."
            ),
            visibility="customer-success",
            updated_at="2026-07-23T10:30:00Z",
            source_url="https://pulse.summit.test/meetings/mtg_northwind_0723",
        ),
    ]

    customer_updates = [
        make_record(
            "pulse",
            "upd_northwind_0728",
            id="upd_northwind_0728",
            customer_id="cus_northwind",
            channel="northwind-operations",
            author={"id": "usr_priya", "name": "Priya Nair"},
            message=(
                "We are investigating elevated search latency and have paused background "
                "maintenance while we validate a mitigation. The next update is scheduled "
                "for 15:00 UTC."
            ),
            created_at="2026-07-28T14:10:00Z",
            visibility="external",
            updated_at="2026-07-28T14:10:00Z",
            source_url="https://pulse.summit.test/customer-updates/upd_northwind_0728",
        )
    ]

    return {
        "meta": META,
        "messages": messages,
        "meetings": meetings,
        "customer_updates": customer_updates,
        "idempotency": {},
    }


def register_routes(app: FastAPI) -> None:
    @app.get("/messages")
    def list_messages(customer_id: str | None = None) -> dict[str, Any]:
        records = filtered(app.state.store.read()["messages"], customer_id=customer_id)
        records.sort(key=lambda message: message["created_at"])
        return {"items": records, "count": len(records)}

    @app.get("/messages/search")
    def search_messages(
        query: Annotated[str, Query(min_length=2)],
        customer_id: str | None = None,
        since: str | None = None,
        limit: Annotated[int, Query(ge=1, le=50)] = 10,
    ) -> dict[str, Any]:
        since_time = datetime.fromisoformat(since) if since else None
        matches = []
        for message in app.state.store.read()["messages"]:
            if customer_id is not None and message.get("customer_id") != customer_id:
                continue
            created_at = datetime.fromisoformat(message["created_at"])
            if since_time is not None and created_at < since_time:
                continue
            score = relevance_score(query, message["channel"], message["text"])
            if score <= 0:
                continue
            matches.append({"score": score, **message})
        matches.sort(key=lambda match: (-match["score"], match["created_at"]))
        results = matches[:limit]
        return {"items": results, "count": len(results), "query": query}

    @app.get("/meetings")
    def list_meetings(
        customer_id: str | None = None,
        starts_after: str | None = None,
        starts_before: str | None = None,
    ) -> dict[str, Any]:
        after = datetime.fromisoformat(starts_after) if starts_after else None
        before = datetime.fromisoformat(starts_before) if starts_before else None
        results = []
        for meeting in app.state.store.read()["meetings"]:
            if customer_id is not None and meeting.get("customer_id") != customer_id:
                continue
            starts_at = datetime.fromisoformat(meeting["starts_at"])
            if after and starts_at < after:
                continue
            if before and starts_at > before:
                continue
            results.append(meeting)
        results.sort(key=lambda meeting: meeting["starts_at"], reverse=True)
        return {"items": results, "count": len(results)}

    @app.get("/customer-updates")
    def list_customer_updates(customer_id: str) -> dict[str, Any]:
        records = filtered(app.state.store.read()["customer_updates"], customer_id=customer_id)
        records.sort(key=lambda update: update["created_at"], reverse=True)
        return {"items": records, "count": len(records)}

    @app.post("/customer-updates", status_code=201)
    def post_customer_update(
        request: CustomerUpdateCreate,
        idempotency_key: Annotated[str | None, Header(alias="Idempotency-Key")] = None,
    ) -> JSONResponse:
        if not idempotency_key:
            raise HTTPException(status_code=400, detail="Idempotency-Key header is required")
        data = app.state.store.read()
        existing_id = data["idempotency"].get(idempotency_key)
        if existing_id:
            existing = find(data["customer_updates"], existing_id)
            return JSONResponse(status_code=200, content=existing)
        update_id = (
            f"upd_{request.customer_id.removeprefix('cus_')}_{len(data['customer_updates']) + 1}"
        )
        now = utc_now()
        record = make_record(
            "pulse",
            update_id,
            id=update_id,
            customer_id=request.customer_id,
            channel=request.channel,
            author={"id": "highland-agent", "name": "Highland Agent"},
            message=request.message,
            created_at=now,
            visibility="external",
            updated_at=now,
            source_url=f"https://pulse.summit.test/customer-updates/{update_id}",
        )
        data["customer_updates"].append(record)
        data["idempotency"][idempotency_key] = update_id
        app.state.store.write(data)
        return JSONResponse(status_code=201, content=record)


def register_tools(mcp: FastMCP, client: SourceClient) -> None:

    @mcp.tool()
    def list_messages(customer_id: str | None = None) -> dict[str, Any]:
        """Enumerate messages eligible for the local search index."""
        return client.get("/messages", {"customer_id": customer_id})

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
        customer_id: str | None = None,
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
