from __future__ import annotations

from pathlib import Path

from conftest import client_for


def test_crm_returns_northwind(seed_dir: Path, runtime_dir: Path) -> None:
    with client_for("crm", seed_dir, runtime_dir) as client:
        response = client.get("/customers/cus_northwind")
    assert response.status_code == 200
    customer = response.json()
    assert customer["name"] == "Northwind Bank"
    assert customer["deployment_ids"] == ["dep_northwind_prod"]
    assert customer["source_url"].endswith("/customers/cus_northwind")


def test_knowledge_search_returns_exact_passage(seed_dir: Path, runtime_dir: Path) -> None:
    with client_for("knowledge", seed_dir, runtime_dir) as client:
        response = client.get(
            "/search",
            params={"query": "compaction memory shard", "customer_id": "cus_northwind"},
        )
    assert response.status_code == 200
    results = response.json()["items"]
    assert results
    assert any(item["passage_id"] == "pas_latency_compaction" for item in results)
    assert all("source_url" in item and "text" in item for item in results)


def test_knowledge_document_has_downloadable_source_file(seed_dir: Path, runtime_dir: Path) -> None:
    with client_for("knowledge", seed_dir, runtime_dir) as client:
        metadata = client.get("/documents/doc_latency_runbook")
        download = client.get("/documents/doc_latency_runbook/download")
    assert metadata.json()["file_name"] == "doc_latency_runbook.md"
    assert download.status_code == 200
    assert download.headers["content-type"].startswith("text/markdown")
    assert "## Compaction contention" in download.text
    assert 'id="pas_latency_compaction"' in download.text


def test_support_write_is_idempotent(seed_dir: Path, runtime_dir: Path) -> None:
    request = {
        "customer_id": "cus_northwind",
        "title": "Controlled compaction follow-up",
        "description": "Track the outcome of the controlled compaction and customer update.",
        "priority": "P2",
        "category": "performance",
    }
    headers = {"Idempotency-Key": "run-123-step-7"}
    with client_for("support", seed_dir, runtime_dir) as client:
        first = client.post("/tickets", json=request, headers=headers)
        second = client.post("/tickets", json=request, headers=headers)
        tickets = client.get("/tickets", params={"customer_id": "cus_northwind"}).json()
    assert first.status_code == 201
    assert second.status_code == 200
    assert first.json()["id"] == second.json()["id"]
    assert tickets["count"] == 4


def test_support_write_requires_idempotency_key(seed_dir: Path, runtime_dir: Path) -> None:
    with client_for("support", seed_dir, runtime_dir) as client:
        response = client.post(
            "/tickets",
            json={
                "customer_id": "cus_northwind",
                "title": "Missing key request",
                "description": "This mutation should be rejected without a stable key.",
            },
        )
    assert response.status_code == 400


def test_metrics_show_latency_and_memory_spike(seed_dir: Path, runtime_dir: Path) -> None:
    with client_for("observability", seed_dir, runtime_dir) as client:
        latency = client.post(
            "/metrics/query",
            json={
                "customer_id": "cus_northwind",
                "metric": "retrieval.p95_ms",
                "time_range": "24h",
            },
        )
        memory = client.post(
            "/metrics/query",
            json={
                "customer_id": "cus_northwind",
                "metric": "retrieval.shard_3.memory_utilization",
                "time_range": "24h",
            },
        )
    assert latency.status_code == 200
    assert latency.json()["series"][0]["summary"]["max"] > 1200
    assert memory.json()["series"][0]["summary"]["max"] > 0.85


def test_failure_injection(seed_dir: Path, runtime_dir: Path) -> None:
    with client_for("crm", seed_dir, runtime_dir) as client:
        response = client.get("/customers", headers={"X-Mock-Failure": "503"})
    assert response.status_code == 503
    assert response.json()["error"] == "injected_failure"


def test_customer_update_write_is_idempotent(seed_dir: Path, runtime_dir: Path) -> None:
    request = {
        "customer_id": "cus_northwind",
        "message": (
            "The mitigation remains stable. We will share results from the controlled "
            "maintenance run at 23:00 UTC."
        ),
        "channel": "northwind-operations",
    }
    headers = {"Idempotency-Key": "run-456-approved-update"}
    with client_for("communications", seed_dir, runtime_dir) as client:
        first = client.post("/customer-updates", json=request, headers=headers)
        second = client.post("/customer-updates", json=request, headers=headers)
        updates = client.get("/customer-updates", params={"customer_id": "cus_northwind"})
    assert first.status_code == 201
    assert second.status_code == 200
    assert first.json()["id"] == second.json()["id"]
    assert updates.json()["count"] == 2
