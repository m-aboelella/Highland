from __future__ import annotations

from datetime import UTC, datetime

from highland.retrieval.contracts import (
    SourceDocument,
    SyncManifest,
    SyncRecord,
    SyncState,
    chunk_document,
)
from highland.retrieval.manifest import load_manifest, save_manifest


def document(text: str) -> SourceDocument:
    return SourceDocument(
        source_system="archive",
        source_id="doc_1",
        title="Runbook",
        text=text,
        source_type="runbook",
        customer_id="cus_northwind",
        visibility="support",
        updated_at=datetime(2026, 7, 29, tzinfo=UTC),
        source_url="https://archive.test/doc_1",
    )


def test_identical_content_produces_identical_chunk_ids() -> None:
    first = chunk_document(document("# Detection\nCheck the queue.\n\n# Recovery\nDrain it."))
    second = chunk_document(document("# Detection\nCheck the queue.\n\n# Recovery\nDrain it."))
    assert [chunk.id for chunk in first] == [chunk.id for chunk in second]
    assert [chunk.location.section for chunk in first] == ["Detection", "Recovery"]


def test_updating_one_section_changes_only_its_chunk_identity() -> None:
    before = chunk_document(document("# Detection\nCheck the queue.\n\n# Recovery\nDrain it."))
    after = chunk_document(document("# Detection\nCheck the queue.\n\n# Recovery\nRestart it."))
    assert before[0].id == after[0].id
    assert before[1].id != after[1].id


def test_chunk_resolves_to_canonical_record_and_location() -> None:
    chunk = chunk_document(document("Inspect SUP-1042."))[0]
    assert chunk.source_id == "doc_1"
    assert chunk.source_url == "https://archive.test/doc_1"
    assert chunk.location.section == "Content"


def test_manifest_round_trip_is_atomic(tmp_path) -> None:
    manifest = SyncManifest.start()
    manifest.records["archive:doc_1"] = SyncRecord(
        source_system="archive",
        source_id="doc_1",
        content_hash="abc",
        updated_at=datetime(2026, 7, 29, tzinfo=UTC),
        chunk_ids=["chk_1"],
        state=SyncState.COMPLETED,
    )
    path = tmp_path / "manifest.json"
    save_manifest(path, manifest)
    assert load_manifest(path) == manifest
    assert list(tmp_path.iterdir()) == [path]
