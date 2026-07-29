from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field

from highland.models.contracts import EmbeddingModel, EmbeddingRequest, InputType

from .contracts import Chunk, SourceDocument, SyncManifest, SyncState, chunk_document
from .ingestion import BackfillService, normalize_record, promote_snapshot, record_hash
from .manifest import load_manifest
from .sources import INDEXABLE_SOURCES, SourceReader, SourceReadError


class SyncResult(BaseModel):
    model_config = ConfigDict(extra="forbid")
    state: SyncState
    started_at: datetime
    completed_at: datetime
    added_records: int = 0
    changed_records: int = 0
    unchanged_records: int = 0
    deleted_records: int = 0
    embedded_chunks: int = 0
    failures: dict[str, str] = Field(default_factory=dict)
    promoted: bool = False


class IndexSynchronizer:
    def __init__(
        self,
        reader: SourceReader,
        *,
        index_dir: Path,
        reports_dir: Path,
        embedding_model: EmbeddingModel | None = None,
    ) -> None:
        self.reader = reader
        self.index_dir = index_dir
        self.reports_dir = reports_dir
        self.embedding_model = embedding_model

    def status(self) -> SyncManifest | None:
        return load_manifest(self.index_dir / "manifest.json")

    async def rebuild(self) -> SyncResult:
        previous = self.status()
        backfill = await BackfillService(
            self.reader, index_dir=self.index_dir, reports_dir=self.reports_dir
        ).backfill()
        current = self.status()
        return SyncResult(
            state=backfill.state,
            started_at=backfill.started_at,
            completed_at=backfill.completed_at,
            added_records=len(current.records) if current and not previous else 0,
            changed_records=len(current.records) if current and previous else 0,
            embedded_chunks=backfill.chunk_count,
            failures=backfill.failures,
            promoted=backfill.promoted,
        )

    async def sync(self) -> SyncResult:
        previous = self.status()
        if previous is None:
            return await self.rebuild()
        started = datetime.now(UTC)
        documents: list[SourceDocument] = []
        counts: dict[str, int] = {}
        failures: dict[str, str] = {}
        for source in INDEXABLE_SOURCES:
            try:
                records = await self.reader.list_records(source)
                counts[source] = len(records)
                documents.extend(
                    document for record in records for document in normalize_record(source, record)
                )
            except SourceReadError as error:
                failures[source] = str(error)
        if failures:
            result = SyncResult(
                state=SyncState.PARTIAL,
                started_at=started,
                completed_at=datetime.now(UTC),
                failures=failures,
            )
            self._write_report(result)
            return result

        chunks = [chunk for document in documents for chunk in chunk_document(document)]
        grouped: dict[str, list[Chunk]] = {}
        for chunk in chunks:
            grouped.setdefault(f"{chunk.source_system}:{chunk.source_id}", []).append(chunk)
        active_previous = {
            key: record
            for key, record in previous.records.items()
            if record.state is not SyncState.TOMBSTONED
        }
        new_hashes = {key: record_hash(value) for key, value in grouped.items()}
        added = {key for key in grouped if key not in active_previous}
        changed = {
            key
            for key, content_hash in new_hashes.items()
            if key in active_previous and active_previous[key].content_hash != content_hash
        }
        unchanged = set(grouped) - added - changed
        deleted = set(active_previous) - set(grouped)
        changed_chunks = [chunk for key in sorted(added | changed) for chunk in grouped[key]]
        if changed_chunks and self.embedding_model is not None:
            await self.embedding_model.embed(
                EmbeddingRequest(
                    texts=[chunk.text for chunk in changed_chunks],
                    input_type=InputType.SEARCH_DOCUMENT,
                    logical_call_id=f"sync:{started.isoformat()}",
                )
            )
        completed = datetime.now(UTC)
        tombstones = {
            key: active_previous[key].model_copy(
                update={"state": SyncState.TOMBSTONED, "chunk_ids": []}
            )
            for key in deleted
        }
        tombstones.update(
            {
                key: record
                for key, record in previous.records.items()
                if record.state is SyncState.TOMBSTONED and key not in grouped
            }
        )
        promote_snapshot(
            self.index_dir,
            chunks=chunks,
            documents=documents,
            started=started,
            completed=completed,
            counts=counts,
            tombstones=tombstones,
        )
        result = SyncResult(
            state=SyncState.COMPLETED,
            started_at=started,
            completed_at=completed,
            added_records=len(added),
            changed_records=len(changed),
            unchanged_records=len(unchanged),
            deleted_records=len(deleted),
            embedded_chunks=len(changed_chunks),
            promoted=True,
        )
        self._write_report(result)
        return result

    def _write_report(self, result: SyncResult) -> None:
        self.reports_dir.mkdir(parents=True, exist_ok=True)
        name = result.started_at.strftime("sync-%Y%m%dT%H%M%S.%fZ.json")
        (self.reports_dir / name).write_text(
            result.model_dump_json(indent=2) + "\n", encoding="utf-8"
        )


def load_chunks(index_dir: Path) -> list[Chunk]:
    path = index_dir / "chunks.jsonl"
    if not path.exists():
        return []
    return [Chunk.model_validate(json.loads(line)) for line in path.read_text().splitlines()]
