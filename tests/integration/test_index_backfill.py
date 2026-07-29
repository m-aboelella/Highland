from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from highland.retrieval.contracts import SyncManifest, SyncState
from highland.retrieval.ingestion import BackfillService
from highland.retrieval.sources import SourceReadError

SOURCE_FILES = {
    "archive": ("knowledge.json", "documents"),
    "relay": ("support.json", "tickets"),
    "beacon": ("observability.json", "incidents"),
    "pulse": ("communications.json", "messages"),
    "track": ("projects.json", "issues"),
}


class SeedReader:
    def __init__(self, seed_dir: Path, *, unavailable: str | None = None) -> None:
        self.seed_dir = seed_dir
        self.unavailable = unavailable

    async def list_records(self, source: str) -> list[dict[str, Any]]:
        if source == self.unavailable:
            raise SourceReadError(source, "synthetic unavailable connector")
        filename, collection = SOURCE_FILES[source]
        payload = json.loads((self.seed_dir / filename).read_text(encoding="utf-8"))
        records = payload[collection]
        if source == "pulse":
            records = records + payload["meetings"]
        return records


def rows(path: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]


@pytest.mark.asyncio
async def test_backfill_is_deterministic_and_excludes_crm_and_metrics(seed_dir, tmp_path) -> None:
    index = tmp_path / "indexes" / "search"
    service = BackfillService(
        SeedReader(seed_dir),
        index_dir=index,
        reports_dir=tmp_path / "sync",
    )
    first = await service.backfill()
    first_chunks = rows(index / "chunks.jsonl")
    second = await service.backfill()
    second_chunks = rows(index / "chunks.jsonl")

    assert first.state is SyncState.COMPLETED
    assert first.promoted and second.promoted
    assert first_chunks == second_chunks
    assert {chunk["source_system"] for chunk in first_chunks} == {
        "archive",
        "relay",
        "beacon",
        "pulse",
        "track",
    }
    payload = json.dumps(first_chunks)
    assert '"source_system": "crm"' not in payload
    assert '"samples"' not in payload
    assert '"source_type": "meeting_notes"' in payload
    assert (
        SyncManifest.model_validate_json((index / "manifest.json").read_text()).state
        is SyncState.COMPLETED
    )


@pytest.mark.asyncio
async def test_partial_backfill_preserves_previous_index(seed_dir, tmp_path) -> None:
    index = tmp_path / "indexes" / "search"
    reports = tmp_path / "sync"
    healthy = BackfillService(SeedReader(seed_dir), index_dir=index, reports_dir=reports)
    await healthy.backfill()
    previous = (index / "chunks.jsonl").read_bytes()

    partial = BackfillService(
        SeedReader(seed_dir, unavailable="pulse"),
        index_dir=index,
        reports_dir=reports,
    )
    result = await partial.backfill()

    assert result.state is SyncState.PARTIAL
    assert not result.promoted
    assert "pulse" in result.failures
    assert (index / "chunks.jsonl").read_bytes() == previous
    assert any(
        '"state":"partial"' in path.read_text().replace(" ", "") for path in reports.iterdir()
    )
