from __future__ import annotations

import copy
import json
from pathlib import Path
from typing import Any

import pytest

from highland.models.scripted import DeterministicEmbeddingModel
from highland.retrieval.contracts import SyncState
from highland.retrieval.ingestion import BackfillService
from highland.retrieval.sync import IndexSynchronizer, load_chunks

SOURCE_FILES = {
    "archive": ("knowledge.json", "documents"),
    "relay": ("support.json", "tickets"),
    "beacon": ("observability.json", "incidents"),
    "pulse": ("communications.json", "messages"),
    "track": ("projects.json", "issues"),
}


class MutableReader:
    def __init__(self, seed_dir: Path) -> None:
        self.records = {
            source: json.loads((seed_dir / filename).read_text())[collection]
            for source, (filename, collection) in SOURCE_FILES.items()
        }

    async def list_records(self, source: str) -> list[dict[str, Any]]:
        return copy.deepcopy(self.records[source])


@pytest.mark.asyncio
async def test_no_change_sync_makes_zero_embedding_calls(seed_dir, tmp_path) -> None:
    reader = MutableReader(seed_dir)
    index = tmp_path / "indexes" / "search"
    reports = tmp_path / "sync"
    embeddings = DeterministicEmbeddingModel()
    await BackfillService(
        reader,
        index_dir=index,
        reports_dir=reports,
        embedding_model=embeddings,
    ).backfill()
    embeddings.requests.clear()

    result = await IndexSynchronizer(
        reader,
        index_dir=index,
        reports_dir=reports,
        embedding_model=embeddings,
    ).sync()

    assert result.state is SyncState.COMPLETED
    assert result.unchanged_records > 0
    assert result.embedded_chunks == 0
    assert embeddings.requests == []


@pytest.mark.asyncio
async def test_sync_rebuilds_when_embedding_model_changes(seed_dir, tmp_path) -> None:
    reader = MutableReader(seed_dir)
    index = tmp_path / "indexes" / "search"
    reports = tmp_path / "sync"
    await BackfillService(
        reader,
        index_dir=index,
        reports_dir=reports,
        embedding_model=DeterministicEmbeddingModel(model="learning-model-a"),
    ).backfill()
    replacement = DeterministicEmbeddingModel(model="learning-model-b")

    result = await IndexSynchronizer(
        reader,
        index_dir=index,
        reports_dir=reports,
        embedding_model=replacement,
    ).sync()

    assert result.promoted
    assert result.changed_records > 0
    assert result.embedded_chunks > 0
    assert replacement.requests


@pytest.mark.asyncio
async def test_updating_one_ticket_embeds_only_affected_chunks(seed_dir, tmp_path) -> None:
    reader = MutableReader(seed_dir)
    index = tmp_path / "indexes" / "search"
    reports = tmp_path / "sync"
    embeddings = DeterministicEmbeddingModel()
    await BackfillService(
        reader,
        index_dir=index,
        reports_dir=reports,
        embedding_model=embeddings,
    ).backfill()
    embeddings.requests.clear()
    ticket = reader.records["relay"][0]
    ticket["description"] += " The new observation affects only this ticket."
    ticket["updated_at"] = "2026-07-29T12:30:00Z"

    result = await IndexSynchronizer(
        reader,
        index_dir=index,
        reports_dir=reports,
        embedding_model=embeddings,
    ).sync()

    assert result.changed_records == 1
    assert len(embeddings.requests) == 1
    assert len(embeddings.requests[0].texts) == result.embedded_chunks
    assert all("SEARCH-482" not in text for text in embeddings.requests[0].texts)


@pytest.mark.asyncio
async def test_deletion_is_tombstoned_and_rebuild_reproduces_content(seed_dir, tmp_path) -> None:
    reader = MutableReader(seed_dir)
    index = tmp_path / "indexes" / "search"
    reports = tmp_path / "sync"
    service = BackfillService(reader, index_dir=index, reports_dir=reports)
    await service.backfill()
    reader.records["track"].pop()
    synchronizer = IndexSynchronizer(reader, index_dir=index, reports_dir=reports)
    result = await synchronizer.sync()
    expected = [chunk.model_dump(mode="json") for chunk in load_chunks(index)]

    assert result.deleted_records == 1
    assert any(
        record.state is SyncState.TOMBSTONED for record in synchronizer.status().records.values()
    )

    rebuilt_index = tmp_path / "rebuilt" / "search"
    rebuilt = IndexSynchronizer(reader, index_dir=rebuilt_index, reports_dir=reports)
    rebuild_result = await rebuilt.rebuild()
    actual = [chunk.model_dump(mode="json") for chunk in load_chunks(rebuilt_index)]
    assert rebuild_result.promoted
    assert actual == expected
