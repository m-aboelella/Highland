from __future__ import annotations

from datetime import UTC, datetime

from fastapi.testclient import TestClient

from highland.api import create_app
from highland.models.contracts import Citation
from highland.retrieval.citations import CitationResolver, CitationStatus
from highland.retrieval.contracts import SourceDocument, chunk_document
from highland.retrieval.ingestion import promote_snapshot
from highland.settings import HighlandSettings


def build_index(path):
    document = SourceDocument(
        source_system="archive",
        source_id="doc_runbook",
        title="Latency runbook",
        text="Pause compaction and observe retrieval latency.",
        source_type="runbook",
        visibility="support",
        updated_at=datetime(2026, 7, 29, tzinfo=UTC),
        source_url="https://archive.test/doc_runbook#recovery",
        section="Recovery",
    )
    chunks = chunk_document(document)
    now = datetime.now(UTC)
    promote_snapshot(
        path,
        chunks=chunks,
        documents=[document],
        started=now,
        completed=now,
        counts={"archive": 1},
        stale_chunk_ids=["chk_000000000000000000000000"],
    )
    return chunks[0]


def test_citations_resolve_to_exact_source_and_visible_failure_states(tmp_path) -> None:
    chunk = build_index(tmp_path / "search")
    resolver = CitationResolver(tmp_path / "search")
    citation = Citation(
        start=5,
        end=14,
        text="compaction",
        source_ids=[
            chunk.id,
            "chk_000000000000000000000000",
            "chk_ffffffffffffffffffffffff",
        ],
    )
    resolved = resolver.resolve(citation)
    assert resolved.start == 5 and resolved.end == 14
    assert [source.status for source in resolved.sources] == [
        CitationStatus.RESOLVED,
        CitationStatus.STALE,
        CitationStatus.UNRESOLVED,
    ]
    assert resolved.sources[0].section == "Recovery"
    assert resolved.sources[0].source_url == "https://archive.test/doc_runbook#recovery"


def test_chunk_inspection_cannot_escape_configured_index(tmp_path) -> None:
    workspace = tmp_path / "workspace"
    chunk = build_index(workspace / "indexes" / "search")
    client = TestClient(create_app(HighlandSettings(workspace_dir=workspace)))
    response = client.get(f"/index/chunks/{chunk.id}")
    assert response.status_code == 200
    assert response.json()["source_id"] == "doc_runbook"
    assert CitationResolver(workspace / "indexes" / "search").get_chunk("../../etc/passwd") is None
    assert client.get("/index/chunks/not-a-chunk").status_code == 404
