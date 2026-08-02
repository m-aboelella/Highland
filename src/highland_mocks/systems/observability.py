from __future__ import annotations

from datetime import datetime
from typing import Any

from fastapi import FastAPI, HTTPException
from mcp.server.fastmcp import FastMCP

from .common import (
    MetricQuery,
    SourceClient,
    filtered,
    find,
    make_record,
    metric_series,
    range_start,
)

META = {"dataset_version": "2026.07.29.1", "as_of": "2026-07-29T12:00:00Z"}


def seed_fragment() -> dict[str, Any]:
    deployments = [
        make_record(
            "beacon",
            "dep_northwind_prod",
            id="dep_northwind_prod",
            customer_id="cus_northwind",
            name="northwind-eu-production",
            environment="production",
            deployment_type="private-cloud",
            region="eu-west-1",
            version="4.18.2",
            topology={
                "retrieval_nodes": 3,
                "ingestion_workers": 1,
                "metadata_store": "customer-managed PostgreSQL",
                "index_shards": 6,
            },
            health="degraded",
            last_change_at="2026-07-27T01:00:00Z",
            change_summary="Upgraded from 4.17.6 to 4.18.2",
            visibility="engineering",
            updated_at="2026-07-29T11:55:00Z",
            source_url="https://beacon.summit.test/deployments/dep_northwind_prod",
        ),
        make_record(
            "beacon",
            "dep_alpine_prod",
            id="dep_alpine_prod",
            customer_id="cus_alpine",
            name="alpine-us-production",
            environment="production",
            deployment_type="summit-cloud",
            region="us-east-2",
            version="4.18.2",
            topology={"retrieval_nodes": 3, "ingestion_workers": 2, "index_shards": 4},
            health="healthy",
            last_change_at="2026-07-21T02:00:00Z",
            change_summary="Routine patch rollout",
            visibility="engineering",
            updated_at="2026-07-29T11:55:00Z",
            source_url="https://beacon.summit.test/deployments/dep_alpine_prod",
        ),
        make_record(
            "beacon",
            "dep_lumon_stage",
            id="dep_lumon_stage",
            customer_id="cus_lumon",
            name="lumon-us-staging",
            environment="staging",
            deployment_type="private-cloud",
            region="us-west-2",
            version="4.18.1",
            topology={"retrieval_nodes": 2, "ingestion_workers": 1, "index_shards": 2},
            health="healthy",
            last_change_at="2026-07-11T18:00:00Z",
            change_summary="Initial staging deployment",
            visibility="engineering",
            updated_at="2026-07-29T11:55:00Z",
            source_url="https://beacon.summit.test/deployments/dep_lumon_stage",
        ),
    ]

    metric_sets = {
        "dep_northwind_prod": [
            metric_series("retrieval.p95_ms", 610, "ms", spike_start=13, spike_by=690),
            metric_series("retrieval.p50_ms", 210, "ms", spike_start=13, spike_by=170),
            metric_series("retrieval.error_rate", 0.002, "ratio", spike_start=13, spike_by=0.003),
            metric_series(
                "retrieval.shard_3.memory_utilization", 0.72, "ratio", spike_start=13, spike_by=0.16
            ),
            metric_series("retrieval.queries_per_second", 42, "qps"),
        ],
        "dep_alpine_prod": [
            metric_series("retrieval.p95_ms", 480, "ms"),
            metric_series("retrieval.error_rate", 0.001, "ratio"),
        ],
        "dep_lumon_stage": [
            metric_series("retrieval.p95_ms", 390, "ms"),
            metric_series("retrieval.error_rate", 0.001, "ratio"),
        ],
    }

    incidents = [
        make_record(
            "beacon",
            "inc_208",
            id="inc_208",
            key="INC-208",
            customer_id="cus_northwind",
            deployment_id="dep_northwind_prod",
            title="Elevated retrieval latency during index compaction",
            severity="SEV-2",
            status="investigating",
            started_at="2026-07-28T12:40:00Z",
            ended_at=None,
            commander={"id": "usr_jonah", "name": "Jonah Okafor"},
            hypothesis=(
                "The first post-upgrade compaction increased memory pressure on shard 3, "
                "causing cache eviction and higher tail latency."
            ),
            mitigation=(
                "Compaction is paused. A controlled single-concurrency run is scheduled for "
                "2026-07-29T22:00:00Z."
            ),
            evidence=[
                "retrieval volume remained within 4% of baseline",
                "shard 3 memory exceeded 90%",
                "p95 declined after compaction was paused",
            ],
            visibility="engineering",
            updated_at="2026-07-29T11:50:00Z",
            source_url="https://beacon.summit.test/incidents/INC-208",
        )
    ]

    return {
        "meta": META,
        "deployments": deployments,
        "metrics": metric_sets,
        "incidents": incidents,
    }


def register_routes(app: FastAPI) -> None:
    @app.get("/deployments")
    def list_deployments(customer_id: str | None = None) -> dict[str, Any]:
        records = filtered(app.state.store.read()["deployments"], customer_id=customer_id)
        return {"items": records, "count": len(records)}

    @app.get("/deployments/{deployment_id}")
    def get_deployment(deployment_id: str) -> dict[str, Any]:
        record = find(app.state.store.read()["deployments"], deployment_id)
        if record is None:
            raise HTTPException(status_code=404, detail="Deployment not found")
        return record

    @app.get("/incidents")
    def list_incidents(
        customer_id: str | None = None,
        status: str | None = None,
    ) -> dict[str, Any]:
        records = filtered(
            app.state.store.read()["incidents"], customer_id=customer_id, status=status
        )
        return {"items": records, "count": len(records)}

    @app.get("/incidents/{incident_id}")
    def get_incident(incident_id: str) -> dict[str, Any]:
        record = find(app.state.store.read()["incidents"], incident_id)
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
        start = range_start(request.time_range)
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


def register_tools(mcp: FastMCP, client: SourceClient) -> None:

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
        customer_id: str | None = None,
        status: str | None = None,
    ) -> dict[str, Any]:
        """List operational incidents for a customer."""
        return client.get("/incidents", {"customer_id": customer_id, "status": status})
