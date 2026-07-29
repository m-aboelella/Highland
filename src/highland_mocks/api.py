from __future__ import annotations

import asyncio
import re
from datetime import UTC, datetime, timedelta
from typing import Annotated, Any

from fastapi import FastAPI, Header, HTTPException, Query, Request
from fastapi.responses import FileResponse, JSONResponse
from pydantic import BaseModel, Field

from .constants import DEFAULT_PORTS, SERVICE_PRODUCTS, SERVICES
from .store import JsonStore


class MetricQuery(BaseModel):
    customer_id: str
    metric: str
    time_range: str = Field(default="24h", pattern=r"^(1h|6h|12h|24h|48h|7d)$")


class TicketCreate(BaseModel):
    customer_id: str
    title: str = Field(min_length=5, max_length=160)
    description: str = Field(min_length=10, max_length=5000)
    priority: str = Field(default="P3", pattern=r"^P[1-4]$")
    category: str = Field(default="investigation", max_length=50)


class IssueCreate(BaseModel):
    project_id: str
    customer_id: str | None = None
    title: str = Field(min_length=5, max_length=160)
    description: str = Field(min_length=10, max_length=5000)
    priority: str = Field(default="medium", pattern=r"^(low|medium|high|urgent)$")
    labels: list[str] = Field(default_factory=list, max_length=10)


class CustomerUpdateCreate(BaseModel):
    customer_id: str
    message: str = Field(min_length=10, max_length=5000)
    channel: str = Field(default="customer-operations", min_length=2, max_length=80)


def _utc_now() -> str:
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")


def _record(system: str, record_id: str, **values: Any) -> dict[str, Any]:
    return {"source_system": system, "record_id": record_id, **values}


def _find(records: list[dict[str, Any]], value: str) -> dict[str, Any] | None:
    value_lower = value.lower()
    return next(
        (
            record
            for record in records
            if str(record.get("id", "")).lower() == value_lower
            or str(record.get("key", "")).lower() == value_lower
        ),
        None,
    )


def _filtered(
    records: list[dict[str, Any]],
    *,
    customer_id: str | None = None,
    status: str | None = None,
    visibility: str | None = None,
) -> list[dict[str, Any]]:
    return [
        record
        for record in records
        if (customer_id is None or record.get("customer_id") == customer_id)
        and (status is None or record.get("status") == status)
        and (visibility is None or record.get("visibility") == visibility)
    ]


def _tokens(value: str) -> set[str]:
    return {token for token in re.findall(r"[a-z0-9][a-z0-9._-]+", value.lower()) if len(token) > 1}


def _score(query: str, *parts: str) -> float:
    query_tokens = _tokens(query)
    if not query_tokens:
        return 0
    text_tokens = _tokens(" ".join(parts))
    overlap = query_tokens & text_tokens
    exact_bonus = 2 if query.lower() in " ".join(parts).lower() else 0
    return round((len(overlap) + exact_bonus) / len(query_tokens), 4)


def _range_start(time_range: str) -> datetime:
    amount = int(time_range[:-1])
    unit = time_range[-1]
    delta = timedelta(hours=amount) if unit == "h" else timedelta(days=amount)
    return datetime(2026, 7, 29, 12, tzinfo=UTC) - delta


def create_app(service: str, store: JsonStore | None = None) -> FastAPI:
    if service != "catalog" and service not in SERVICES:
        raise ValueError(f"Unknown service: {service}")

    product = "Highland mock service catalog" if service == "catalog" else SERVICE_PRODUCTS[service]
    app = FastAPI(
        title=product,
        version="0.1.0",
        description=f"Synthetic {service} system for the Highland educational project.",
    )
    if store is not None:
        app.state.store = store
    elif service != "catalog":
        app.state.store = JsonStore(service)

    @app.middleware("http")
    async def failure_injection(request: Request, call_next: Any) -> Any:
        failure = request.headers.get("x-mock-failure")
        if failure:
            try:
                status = int(failure)
            except ValueError:
                status = 500
            status = min(max(status, 400), 599)
            return JSONResponse(
                status_code=status,
                content={
                    "error": "injected_failure",
                    "message": f"Synthetic HTTP {status} response requested by the caller.",
                },
            )
        latency = request.headers.get("x-mock-latency-ms")
        if latency:
            try:
                milliseconds = min(max(int(latency), 0), 5000)
            except ValueError:
                milliseconds = 0
            await asyncio.sleep(milliseconds / 1000)
        response = await call_next(request)
        response.headers["X-Mock-System"] = service
        return response

    @app.get("/health")
    def health() -> dict[str, Any]:
        result: dict[str, Any] = {"status": "ok", "service": service, "product": product}
        if service != "catalog":
            result["dataset"] = app.state.store.read()["meta"]
        return result

    if service == "catalog":
        _add_catalog_routes(app)
    elif service == "crm":
        _add_crm_routes(app)
    elif service == "knowledge":
        _add_knowledge_routes(app)
    elif service == "support":
        _add_support_routes(app)
    elif service == "observability":
        _add_observability_routes(app)
    elif service == "communications":
        _add_communications_routes(app)
    elif service == "projects":
        _add_projects_routes(app)

    return app


def _add_catalog_routes(app: FastAPI) -> None:
    @app.get("/")
    def catalog() -> dict[str, Any]:
        return {
            "name": "Highland mock enterprise",
            "company": "Summit Software",
            "as_of": "2026-07-29T12:00:00Z",
            "services": [
                {
                    "id": service,
                    "product": SERVICE_PRODUCTS[service],
                    "base_url": f"http://localhost:{DEFAULT_PORTS[service]}",
                    "openapi_url": f"http://localhost:{DEFAULT_PORTS[service]}/openapi.json",
                    "transport": ["http", "mcp-stdio-adapter"],
                }
                for service in SERVICES
            ],
        }


def _add_crm_routes(app: FastAPI) -> None:
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
        record = _find(app.state.store.read()["customers"], customer_id)
        if record is None:
            raise HTTPException(status_code=404, detail="Customer not found")
        return record


def _add_knowledge_routes(app: FastAPI) -> None:
    @app.get("/documents")
    def list_documents(
        customer_id: str | None = None,
        document_type: str | None = None,
        team: str | None = None,
    ) -> dict[str, Any]:
        records = app.state.store.read()["documents"]
        results = [
            record
            for record in records
            if (customer_id is None or record.get("customer_id") in (None, customer_id))
            and (document_type is None or record["document_type"] == document_type)
            and (team is None or record["team"] == team)
        ]
        return {"items": results, "count": len(results)}

    @app.get("/documents/{document_id}")
    def get_document(document_id: str) -> dict[str, Any]:
        record = _find(app.state.store.read()["documents"], document_id)
        if record is None:
            raise HTTPException(status_code=404, detail="Document not found")
        return record

    @app.get("/documents/{document_id}/download")
    def download_document(document_id: str) -> FileResponse:
        record = _find(app.state.store.read()["documents"], document_id)
        if record is None:
            raise HTTPException(status_code=404, detail="Document not found")
        path = app.state.store.seed_dir / "files" / record["file_name"]
        if not path.exists():
            raise HTTPException(status_code=404, detail="Document file not found")
        return FileResponse(
            path,
            media_type="text/markdown",
            filename=record["file_name"],
        )

    @app.get("/search")
    def search_documents(
        query: Annotated[str, Query(min_length=2)],
        customer_id: str | None = None,
        document_type: str | None = None,
        team: str | None = None,
        limit: Annotated[int, Query(ge=1, le=50)] = 10,
    ) -> dict[str, Any]:
        matches = []
        for document in app.state.store.read()["documents"]:
            if customer_id is not None and document.get("customer_id") not in (None, customer_id):
                continue
            if document_type is not None and document["document_type"] != document_type:
                continue
            if team is not None and document["team"] != team:
                continue
            for passage in document["passages"]:
                score = _score(query, document["title"], passage["section"], passage["text"])
                if score <= 0:
                    continue
                matches.append(
                    {
                        "score": score,
                        "source_system": "archive",
                        "record_id": document["id"],
                        "document_id": document["id"],
                        "passage_id": passage["passage_id"],
                        "customer_id": document.get("customer_id"),
                        "title": document["title"],
                        "section": passage["section"],
                        "text": passage["text"],
                        "updated_at": document["updated_at"],
                        "visibility": document["visibility"],
                        "source_url": (f"{document['source_url']}#passage={passage['passage_id']}"),
                    }
                )
        matches.sort(key=lambda match: (-match["score"], match["passage_id"]))
        results = matches[:limit]
        return {"items": results, "count": len(results), "query": query}


def _add_support_routes(app: FastAPI) -> None:
    @app.get("/tickets")
    def list_tickets(
        customer_id: str | None = None,
        status: str | None = None,
        priority: str | None = None,
    ) -> dict[str, Any]:
        records = _filtered(
            app.state.store.read()["tickets"], customer_id=customer_id, status=status
        )
        if priority is not None:
            records = [record for record in records if record["priority"] == priority]
        return {"items": records, "count": len(records)}

    @app.get("/tickets/{ticket_id}")
    def get_ticket(ticket_id: str) -> dict[str, Any]:
        record = _find(app.state.store.read()["tickets"], ticket_id)
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
            existing = _find(data["tickets"], existing_id)
            return JSONResponse(status_code=200, content=existing)
        next_number = (
            max([int(record["key"].split("-")[1]) for record in data["tickets"]] + [1000]) + 1
        )
        ticket_id = f"tkt_{next_number}"
        now = _utc_now()
        record = _record(
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


def _add_observability_routes(app: FastAPI) -> None:
    @app.get("/deployments")
    def list_deployments(customer_id: str | None = None) -> dict[str, Any]:
        records = _filtered(app.state.store.read()["deployments"], customer_id=customer_id)
        return {"items": records, "count": len(records)}

    @app.get("/deployments/{deployment_id}")
    def get_deployment(deployment_id: str) -> dict[str, Any]:
        record = _find(app.state.store.read()["deployments"], deployment_id)
        if record is None:
            raise HTTPException(status_code=404, detail="Deployment not found")
        return record

    @app.get("/incidents")
    def list_incidents(
        customer_id: str | None = None,
        status: str | None = None,
    ) -> dict[str, Any]:
        records = _filtered(
            app.state.store.read()["incidents"], customer_id=customer_id, status=status
        )
        return {"items": records, "count": len(records)}

    @app.get("/incidents/{incident_id}")
    def get_incident(incident_id: str) -> dict[str, Any]:
        record = _find(app.state.store.read()["incidents"], incident_id)
        if record is None:
            raise HTTPException(status_code=404, detail="Incident not found")
        return record

    @app.get("/metrics")
    def list_metrics(customer_id: str) -> dict[str, Any]:
        data = app.state.store.read()
        deployment_ids = [
            deployment["id"]
            for deployment in data["deployments"]
            if deployment["customer_id"] == customer_id
        ]
        names = sorted(
            {
                metric["metric"]
                for deployment_id in deployment_ids
                for metric in data["metrics"].get(deployment_id, [])
            }
        )
        return {"items": names, "count": len(names)}

    @app.post("/metrics/query")
    def query_metrics(request: MetricQuery) -> dict[str, Any]:
        data = app.state.store.read()
        deployment_ids = [
            deployment["id"]
            for deployment in data["deployments"]
            if deployment["customer_id"] == request.customer_id
        ]
        start = _range_start(request.time_range)
        series = []
        for deployment_id in deployment_ids:
            metric = next(
                (
                    item
                    for item in data["metrics"].get(deployment_id, [])
                    if item["metric"] == request.metric
                ),
                None,
            )
            if metric is None:
                continue
            samples = [
                sample
                for sample in metric["samples"]
                if datetime.fromisoformat(sample["timestamp"]) >= start
            ]
            values = [sample["value"] for sample in samples]
            series.append(
                {
                    "deployment_id": deployment_id,
                    "metric": metric["metric"],
                    "unit": metric["unit"],
                    "samples": samples,
                    "summary": {
                        "min": min(values) if values else None,
                        "max": max(values) if values else None,
                        "latest": values[-1] if values else None,
                        "average": round(sum(values) / len(values), 3) if values else None,
                    },
                    "source_url": (
                        "https://beacon.summit.test/metrics?"
                        f"deployment={deployment_id}&metric={request.metric}"
                    ),
                }
            )
        if not series:
            raise HTTPException(status_code=404, detail="Metric not found for customer")
        return {
            "customer_id": request.customer_id,
            "time_range": request.time_range,
            "series": series,
        }


def _add_communications_routes(app: FastAPI) -> None:
    @app.get("/messages")
    def list_messages(customer_id: str | None = None) -> dict[str, Any]:
        records = _filtered(app.state.store.read()["messages"], customer_id=customer_id)
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
            score = _score(query, message["channel"], message["text"])
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
        records = _filtered(app.state.store.read()["customer_updates"], customer_id=customer_id)
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
            existing = _find(data["customer_updates"], existing_id)
            return JSONResponse(status_code=200, content=existing)
        update_id = (
            f"upd_{request.customer_id.removeprefix('cus_')}_{len(data['customer_updates']) + 1}"
        )
        now = _utc_now()
        record = _record(
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


def _add_projects_routes(app: FastAPI) -> None:
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
        records = _filtered(
            app.state.store.read()["issues"], customer_id=customer_id, status=status
        )
        if project_id is not None:
            records = [record for record in records if record["project_id"] == project_id]
        return {"items": records, "count": len(records)}

    @app.get("/issues/{issue_id}")
    def get_issue(issue_id: str) -> dict[str, Any]:
        record = _find(app.state.store.read()["issues"], issue_id)
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
        project = _find(data["projects"], request.project_id)
        if project is None:
            raise HTTPException(status_code=404, detail="Project not found")
        existing_id = data["idempotency"].get(idempotency_key)
        if existing_id:
            existing = _find(data["issues"], existing_id)
            return JSONResponse(status_code=200, content=existing)
        project_key = project["key"]
        numbers = [
            int(record["key"].split("-")[1])
            for record in data["issues"]
            if record["key"].startswith(f"{project_key}-")
        ]
        next_number = max(numbers + [0]) + 1
        issue_id = f"iss_{project_key.lower()}_{next_number}"
        now = _utc_now()
        record = _record(
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
