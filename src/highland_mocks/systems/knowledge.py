from __future__ import annotations

from typing import Annotated, Any

from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import FileResponse
from mcp.server.fastmcp import FastMCP

from .common import (
    SourceClient,
    find,
    make_record,
    relevance_score,
)

META = {"dataset_version": "2026.07.29.1", "as_of": "2026-07-29T12:00:00Z"}


def seed_fragment() -> dict[str, Any]:
    documents = [
        make_record(
            "archive",
            "doc_latency_runbook",
            id="doc_latency_runbook",
            title="Runbook: elevated retrieval latency",
            document_type="runbook",
            team="site-reliability",
            customer_id=None,
            version="3.4",
            visibility="support",
            updated_at="2026-06-18T14:00:00Z",
            source_url="https://archive.summit.test/docs/doc_latency_runbook",
            passages=[
                {
                    "passage_id": "pas_latency_triage",
                    "ordinal": 1,
                    "section": "Triage order",
                    "text": (
                        "For a sustained retrieval p95 above 900 ms, first compare query "
                        "volume, error rate, and shard memory. If volume is stable and one "
                        "shard exceeds 85% memory, inspect compaction and cache eviction "
                        "before scaling the whole cluster."
                    ),
                },
                {
                    "passage_id": "pas_latency_compaction",
                    "ordinal": 2,
                    "section": "Compaction contention",
                    "text": (
                        "Background index compaction can contend with retrieval when a shard "
                        "has less than 15% free memory. Pause the compaction job, allow caches "
                        "to recover for 20 minutes, then compare p50 and p95. Do not restart "
                        "all nodes at once."
                    ),
                },
                {
                    "passage_id": "pas_latency_customer_update",
                    "ordinal": 3,
                    "section": "Customer communication",
                    "text": (
                        "Tell the customer what users experienced, the current mitigation, "
                        "and the next checkpoint. Label suspected causes as hypotheses until "
                        "correlated with logs. Never promise a resolution time before the "
                        "mitigation has been observed under representative load."
                    ),
                },
            ],
        ),
        make_record(
            "archive",
            "doc_private_cloud_arch",
            id="doc_private_cloud_arch",
            title="Private-cloud deployment architecture",
            document_type="architecture",
            team="platform",
            customer_id=None,
            version="5.1",
            visibility="company",
            updated_at="2026-05-20T08:30:00Z",
            source_url="https://archive.summit.test/docs/doc_private_cloud_arch",
            passages=[
                {
                    "passage_id": "pas_private_topology",
                    "ordinal": 1,
                    "section": "Standard topology",
                    "text": (
                        "The standard regulated topology uses three retrieval nodes across "
                        "separate failure domains, a dedicated ingestion worker, and an "
                        "external PostgreSQL metadata store. Customer traffic enters through "
                        "their managed gateway."
                    ),
                },
                {
                    "passage_id": "pas_private_telemetry",
                    "ordinal": 2,
                    "section": "Telemetry",
                    "text": (
                        "Private-cloud deployments export aggregate service metrics to Beacon "
                        "through an outbound-only collector. Document content, search queries, "
                        "and result payloads never leave the customer environment."
                    ),
                },
            ],
        ),
        make_record(
            "archive",
            "doc_4182_release",
            id="doc_4182_release",
            title="Summit Search 4.18.2 release notes",
            document_type="release-notes",
            team="product",
            customer_id=None,
            version="4.18.2",
            visibility="company",
            updated_at="2026-07-10T12:00:00Z",
            source_url="https://archive.summit.test/docs/doc_4182_release",
            passages=[
                {
                    "passage_id": "pas_4182_compaction",
                    "ordinal": 1,
                    "section": "Index maintenance",
                    "text": (
                        "Version 4.18.2 reduces average compaction duration for newly created "
                        "indexes. Existing large shards can temporarily use more memory during "
                        "their first post-upgrade compaction. Schedule that operation outside "
                        "peak query periods."
                    ),
                },
                {
                    "passage_id": "pas_4182_known",
                    "ordinal": 2,
                    "section": "Known limitations",
                    "text": (
                        "On clusters with shards above 1.5 TB, compaction concurrency must "
                        "remain at one until the shard has completed its first 4.18.2 cycle."
                    ),
                },
            ],
        ),
        make_record(
            "archive",
            "doc_incident_policy",
            id="doc_incident_policy",
            title="Customer incident severity and communication policy",
            document_type="policy",
            team="support",
            customer_id=None,
            version="2.2",
            visibility="support",
            updated_at="2026-04-05T10:00:00Z",
            source_url="https://archive.summit.test/docs/doc_incident_policy",
            passages=[
                {
                    "passage_id": "pas_severity_p1",
                    "ordinal": 1,
                    "section": "Priority 1",
                    "text": (
                        "A production degradation affecting a primary customer workflow with "
                        "no acceptable workaround is Priority 1. Provide an update at least "
                        "every 60 minutes while actively mitigating."
                    ),
                },
                {
                    "passage_id": "pas_incident_approval",
                    "ordinal": 2,
                    "section": "External updates",
                    "text": (
                        "Drafts generated from internal investigation notes require a human "
                        "owner to verify customer-safe wording before they are posted to an "
                        "external channel."
                    ),
                },
            ],
        ),
        make_record(
            "archive",
            "doc_northwind_success_plan",
            id="doc_northwind_success_plan",
            title="Northwind Bank 2026 success plan",
            document_type="customer-plan",
            team="customer-success",
            customer_id="cus_northwind",
            version="1.6",
            visibility="customer-success",
            updated_at="2026-07-01T11:00:00Z",
            source_url="https://archive.summit.test/docs/doc_northwind_success_plan",
            passages=[
                {
                    "passage_id": "pas_northwind_outcomes",
                    "ordinal": 1,
                    "section": "Business outcomes",
                    "text": (
                        "Northwind’s 2026 target is to reduce analyst time spent locating "
                        "approved policy material by 30%. The executive sponsor will assess "
                        "adoption, answer quality, and stable performance at the October "
                        "renewal review."
                    ),
                },
                {
                    "passage_id": "pas_northwind_actions",
                    "ordinal": 2,
                    "section": "Open success actions",
                    "text": (
                        "Summit will deliver a capacity review before August 7. Northwind will "
                        "confirm its Q4 ingestion forecast. Both teams will agree on a latency "
                        "service-level objective before renewal planning."
                    ),
                },
            ],
        ),
    ]
    for document in documents:
        file_name = f"{document['id']}.md"
        document["file_name"] = file_name
        document["download_url"] = f"http://localhost:8102/documents/{document['id']}/download"

    return {"meta": META, "documents": documents}


def register_routes(app: FastAPI) -> None:
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
        record = find(app.state.store.read()["documents"], document_id)
        if record is None:
            raise HTTPException(status_code=404, detail="Document not found")
        return record

    @app.get("/documents/{document_id}/download")
    def download_document(document_id: str) -> FileResponse:
        record = find(app.state.store.read()["documents"], document_id)
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
                score = relevance_score(
                    query, document["title"], passage["section"], passage["text"]
                )
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


def register_tools(mcp: FastMCP, client: SourceClient) -> None:

    @mcp.tool()
    def list_documents(
        customer_id: str | None = None,
        document_type: str | None = None,
        team: str | None = None,
    ) -> dict[str, Any]:
        """Enumerate canonical documents available for indexing."""
        return client.get(
            "/documents",
            {
                "customer_id": customer_id,
                "document_type": document_type,
                "team": team,
            },
        )

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
