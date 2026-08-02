from __future__ import annotations

from typing import Annotated, Any

from fastapi import FastAPI, Header, HTTPException
from fastapi.responses import JSONResponse
from mcp.server.fastmcp import FastMCP

from .common import (
    IssueCreate,
    SourceClient,
    filtered,
    find,
    make_record,
    utc_now,
)

META = {"dataset_version": "2026.07.29.1", "as_of": "2026-07-29T12:00:00Z"}


def seed_fragment() -> dict[str, Any]:
    projects = [
        make_record(
            "track",
            "prj_search_runtime",
            id="prj_search_runtime",
            key="SEARCH",
            name="Search Runtime",
            team="engineering",
            visibility="engineering",
            updated_at="2026-07-29T10:30:00Z",
            source_url="https://track.summit.test/projects/SEARCH",
        ),
        make_record(
            "track",
            "prj_customer_success",
            id="prj_customer_success",
            key="CUST",
            name="Customer Success",
            team="customer-success",
            visibility="customer-success",
            updated_at="2026-07-29T10:30:00Z",
            source_url="https://track.summit.test/projects/CUST",
        ),
    ]

    issues = [
        make_record(
            "track",
            "iss_482",
            id="iss_482",
            key="SEARCH-482",
            project_id="prj_search_runtime",
            customer_id="cus_northwind",
            title="Limit compaction memory on large existing shards",
            description=(
                "Add a configurable memory ceiling and validate the change on private-cloud "
                "clusters with shards larger than 1.5 TB."
            ),
            status="in_progress",
            priority="high",
            assignee={"id": "usr_lee", "name": "Lee Park"},
            due_at="2026-08-04T17:00:00Z",
            labels=["performance", "compaction", "private-cloud"],
            visibility="engineering",
            created_at="2026-07-28T16:00:00Z",
            updated_at="2026-07-29T10:25:00Z",
            source_url="https://track.summit.test/issues/SEARCH-482",
        ),
        make_record(
            "track",
            "iss_91",
            id="iss_91",
            key="CUST-91",
            project_id="prj_customer_success",
            customer_id="cus_northwind",
            title="Deliver Northwind capacity review",
            description=(
                "Summarize current headroom, expected Q4 ingestion, and recommended shard "
                "plan. Share a customer-safe version before August 7."
            ),
            status="todo",
            priority="high",
            assignee={"id": "usr_jonah", "name": "Jonah Okafor"},
            due_at="2026-08-07T17:00:00Z",
            labels=["customer-commitment", "renewal"],
            visibility="customer-success",
            created_at="2026-07-23T10:40:00Z",
            updated_at="2026-07-23T10:40:00Z",
            source_url="https://track.summit.test/issues/CUST-91",
        ),
        make_record(
            "track",
            "iss_92",
            id="iss_92",
            key="CUST-92",
            project_id="prj_customer_success",
            customer_id="cus_northwind",
            title="Agree retrieval latency SLO with Northwind",
            description="Draft an SLO proposal for review during renewal planning.",
            status="todo",
            priority="medium",
            assignee={"id": "usr_maya", "name": "Maya Chen"},
            due_at="2026-08-14T17:00:00Z",
            labels=["customer-commitment", "slo"],
            visibility="customer-success",
            created_at="2026-07-23T10:45:00Z",
            updated_at="2026-07-23T10:45:00Z",
            source_url="https://track.summit.test/issues/CUST-92",
        ),
    ]

    return {"meta": META, "projects": projects, "issues": issues, "idempotency": {}}


def register_routes(app: FastAPI) -> None:
    @app.get("/projects")
    def list_projects() -> dict[str, Any]:
        records = app.state.store.read()["projects"]
        return {"items": records, "count": len(records)}

    @app.get("/issues")
    def list_issues(
        customer_id: str | None = None,
        project_id: str | None = None,
        status: str | None = None,
    ) -> dict[str, Any]:
        records = filtered(app.state.store.read()["issues"], customer_id=customer_id, status=status)
        if project_id is not None:
            records = [record for record in records if record["project_id"] == project_id]
        return {"items": records, "count": len(records)}

    @app.get("/issues/{issue_id}")
    def get_issue(issue_id: str) -> dict[str, Any]:
        record = find(app.state.store.read()["issues"], issue_id)
        if record is None:
            raise HTTPException(status_code=404, detail="Issue not found")
        return record

    @app.post("/issues", status_code=201)
    def create_issue(
        request: IssueCreate,
        idempotency_key: Annotated[str | None, Header(alias="Idempotency-Key")] = None,
    ) -> JSONResponse:
        if not idempotency_key:
            raise HTTPException(status_code=400, detail="Idempotency-Key header is required")
        data = app.state.store.read()
        project = find(data["projects"], request.project_id)
        if project is None:
            raise HTTPException(status_code=404, detail="Project not found")
        existing_id = data["idempotency"].get(idempotency_key)
        if existing_id:
            existing = find(data["issues"], existing_id)
            return JSONResponse(status_code=200, content=existing)
        project_key = project["key"]
        numbers = [
            int(record["key"].split("-")[1])
            for record in data["issues"]
            if record["key"].startswith(f"{project_key}-")
        ]
        next_number = max(numbers + [0]) + 1
        issue_id = f"iss_{project_key.lower()}_{next_number}"
        now = utc_now()
        record = make_record(
            "track",
            issue_id,
            id=issue_id,
            key=f"{project_key}-{next_number}",
            project_id=project["id"],
            customer_id=request.customer_id,
            title=request.title,
            description=request.description,
            status="todo",
            priority=request.priority,
            assignee=None,
            due_at=None,
            labels=request.labels,
            visibility=project["visibility"],
            created_at=now,
            updated_at=now,
            source_url=f"https://track.summit.test/issues/{project_key}-{next_number}",
        )
        data["issues"].append(record)
        data["idempotency"][idempotency_key] = issue_id
        app.state.store.write(data)
        return JSONResponse(status_code=201, content=record)


def register_tools(mcp: FastMCP, client: SourceClient) -> None:

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
