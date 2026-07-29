from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

from fastapi.testclient import TestClient

from highland.api import create_app
from highland.retrieval.contracts import Chunk, ChunkLocation
from highland.runtime.events import RunEventStore
from highland.settings import HighlandSettings


def test_evidence_endpoint_resolves_exact_passage_and_diagnostics(tmp_path: Path) -> None:
    chunk = Chunk(
        id="chk_0123456789abcdef01234567",
        source_system="archive",
        source_id="doc_latency_runbook",
        title="Latency runbook",
        text="Exact compaction contention passage.",
        content_hash="hash",
        source_type="runbook",
        visibility="internal",
        updated_at=datetime(2026, 7, 1, tzinfo=UTC),
        source_url="mock://archive/doc_latency_runbook#passage=compaction",
        location=ChunkLocation(section="Compaction contention", ordinal=0),
        customer_id="cus_northwind",
    )
    index = tmp_path / "indexes" / "search"
    index.mkdir(parents=True)
    (index / "chunks.jsonl").write_text(chunk.model_dump_json() + "\n", encoding="utf-8")
    events = RunEventStore(tmp_path / "runs" / "events")
    events.append(
        "run-evidence",
        "retrieval",
        {
            "filters": {"customer_id": "cus_northwind", "source_types": ["runbook"]},
            "diagnostics": [{"chunk_id": chunk.id, "rerank_score": 0.98}],
            "timings": {"total_ms": 4.2},
        },
    )
    events.append(
        "run-evidence",
        "citation",
        {"start": 0, "end": 10, "text": "Compaction", "source_ids": [chunk.id]},
    )
    events.append("run-evidence", "tool_result", {"tool_call_id": "crm-current"})

    with TestClient(
        create_app(HighlandSettings(workspace_dir=tmp_path, connector_commands={}))
    ) as client:
        response = client.get("/runs/run-evidence/evidence")

    assert response.status_code == 200
    payload = response.json()
    assert payload["evidence"][0]["text"] == "Exact compaction contention passage."
    assert payload["evidence"][0]["location"]["section"] == "Compaction contention"
    assert payload["filters"]["customer_id"] == "cus_northwind"
    assert payload["diagnostics"][0]["rerank_score"] == 0.98
    assert payload["refreshed_through_mcp"] is True
