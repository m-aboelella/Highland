from __future__ import annotations

import json
import os
import shutil
import tempfile
from collections import defaultdict
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from highland.models.contracts import EmbeddingModel

from .contracts import Chunk, SourceDocument, SyncManifest, SyncRecord, SyncState, chunk_document
from .manifest import save_manifest
from .sources import INDEXABLE_SOURCES, SourceReader, SourceReadError


class BackfillResult(BaseModel):
    model_config = ConfigDict(extra="forbid")
    state: SyncState
    started_at: datetime
    completed_at: datetime
    source_counts: dict[str, int] = Field(default_factory=dict)
    chunk_count: int = 0
    failures: dict[str, str] = Field(default_factory=dict)
    promoted: bool = False


def _date(record: dict[str, Any], key: str = "updated_at") -> datetime:
    value = record.get(key) or record.get("created_at") or record.get("started_at")
    return datetime.fromisoformat(str(value))


def normalize_record(source: str, record: dict[str, Any]) -> list[SourceDocument]:
    common = {
        "source_system": source,
        "source_id": str(record["id"]),
        "customer_id": record.get("customer_id"),
        "visibility": str(record["visibility"]),
        "updated_at": _date(record),
        "source_url": str(record["source_url"]),
    }
    if source == "archive":
        return [
            SourceDocument(
                **common,
                title=str(record["title"]),
                text=str(passage["text"]),
                source_type=str(record["document_type"]),
                section=str(passage["section"]),
                passage_id=str(passage["passage_id"]),
                metadata={"team": record["team"], "version": record["version"]},
            )
            for passage in record["passages"]
        ]
    if source == "relay":
        comments = "\n\n".join(
            f"## Comment by {comment['author']} ({comment['visibility']})\n{comment['text']}"
            for comment in record.get("comments", [])
        )
        text = f"## Description\n{record['description']}"
        if comments:
            text = f"{text}\n\n{comments}"
        return [
            SourceDocument(
                **common,
                title=f"{record.get('key', record['id'])}: {record['title']}",
                text=text,
                source_type="ticket",
                metadata={"key": record.get("key", ""), "priority": record.get("priority", "")},
            )
        ]
    if source == "beacon":
        evidence = "\n".join(f"- {item}" for item in record.get("evidence", []))
        return [
            SourceDocument(
                **common,
                title=f"{record.get('key', record['id'])}: {record['title']}",
                text=(
                    f"## Symptoms and hypothesis\n{record.get('hypothesis') or 'Not recorded'}\n\n"
                    f"## Mitigation or resolution\n{record.get('mitigation') or 'Not recorded'}\n\n"
                    f"## Evidence\n{evidence}"
                ),
                source_type="incident",
                metadata={"key": record.get("key", ""), "severity": record.get("severity", "")},
            )
        ]
    if source == "pulse":
        if "notes" in record:
            if not record.get("notes"):
                return []
            return [
                SourceDocument(
                    **common,
                    title=str(record["title"]),
                    text=str(record["notes"]),
                    source_type="meeting_notes",
                    metadata={"starts_at": record["starts_at"]},
                )
            ]
        return [
            SourceDocument(
                **common,
                title=f"Message in {record['channel']}",
                text=str(record["text"]),
                source_type="message",
                metadata={"channel": record["channel"], "thread_id": record.get("thread_id")},
            )
        ]
    if source == "track":
        return [
            SourceDocument(
                **common,
                title=f"{record.get('key', record['id'])}: {record['title']}",
                text=str(record["description"]),
                source_type="issue",
                metadata={"key": record.get("key", ""), "project_id": record["project_id"]},
            )
        ]
    raise ValueError(f"Unsupported source: {source}")


class BackfillService:
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

    async def backfill(self, sources: list[str] | None = None) -> BackfillResult:
        selected = tuple(sources or INDEXABLE_SOURCES)
        unknown = set(selected) - set(INDEXABLE_SOURCES)
        if unknown:
            raise ValueError(f"Unknown index source(s): {', '.join(sorted(unknown))}")
        started = datetime.now(UTC)
        failures: dict[str, str] = {}
        documents: list[SourceDocument] = []
        counts: dict[str, int] = {}
        for source in selected:
            try:
                records = await self.reader.list_records(source)
                normalized = [
                    document for record in records for document in normalize_record(source, record)
                ]
                documents.extend(normalized)
                counts[source] = len(records)
            except SourceReadError as error:
                failures[source] = str(error)

        chunks = [chunk for document in documents for chunk in chunk_document(document)]
        completed = datetime.now(UTC)
        state = SyncState.PARTIAL if failures else SyncState.COMPLETED
        result = BackfillResult(
            state=state,
            started_at=started,
            completed_at=completed,
            source_counts=counts,
            chunk_count=len(chunks),
            failures=failures,
            promoted=False,
        )
        self._write_report(result)
        if failures:
            return result
        vector_source: Path | None = None
        vector_stage: Path | None = None
        if self.embedding_model is not None:
            from .faiss_store import EmbeddingIndex

            self.index_dir.parent.mkdir(parents=True, exist_ok=True)
            vector_stage = Path(
                tempfile.mkdtemp(prefix=".embedding-stage-", dir=self.index_dir.parent)
            )
            vector_source = vector_stage / "vectors"
            await EmbeddingIndex(self.embedding_model).build(chunks, vector_source)
        try:
            promote_snapshot(
                self.index_dir,
                chunks=chunks,
                documents=documents,
                started=started,
                completed=completed,
                counts=counts,
                vector_source=vector_source,
            )
        finally:
            if vector_stage is not None and vector_stage.exists():
                shutil.rmtree(vector_stage)
        result = result.model_copy(update={"promoted": True})
        self._write_report(result)
        return result

    def _write_report(self, result: BackfillResult) -> None:
        self.reports_dir.mkdir(parents=True, exist_ok=True)
        name = result.started_at.strftime("backfill-%Y%m%dT%H%M%S.%fZ.json")
        path = self.reports_dir / name
        path.write_text(result.model_dump_json(indent=2) + "\n", encoding="utf-8")


def record_hash(chunks: list[Chunk]) -> str:
    import hashlib

    return hashlib.sha256("".join(chunk.content_hash for chunk in chunks).encode()).hexdigest()


def _write_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    path.write_text(
        "".join(json.dumps(row, sort_keys=True) + "\n" for row in rows), encoding="utf-8"
    )


def promote_snapshot(
    index_dir: Path,
    *,
    chunks: list[Chunk],
    documents: list[SourceDocument],
    started: datetime,
    completed: datetime,
    counts: dict[str, int],
    tombstones: dict[str, SyncRecord] | None = None,
    vector_source: Path | None = None,
    stale_chunk_ids: list[str] | None = None,
) -> None:
    index_dir.parent.mkdir(parents=True, exist_ok=True)
    stage = Path(tempfile.mkdtemp(prefix=".index-stage-", dir=index_dir.parent))
    try:
        _write_jsonl(stage / "chunks.jsonl", [chunk.model_dump(mode="json") for chunk in chunks])
        _write_jsonl(
            stage / "documents.jsonl",
            [document.model_dump(mode="json") for document in documents],
        )
        by_record: dict[tuple[str, str], list[Chunk]] = defaultdict(list)
        for chunk in chunks:
            by_record[(chunk.source_system, chunk.source_id)].append(chunk)
        records = {
            f"{source}:{source_id}": SyncRecord(
                source_system=source,
                source_id=source_id,
                content_hash=record_hash(record_chunks),
                updated_at=max(chunk.updated_at for chunk in record_chunks),
                chunk_ids=[chunk.id for chunk in record_chunks],
            )
            for (source, source_id), record_chunks in sorted(by_record.items())
        }
        records.update(tombstones or {})
        manifest = SyncManifest(
            state=SyncState.COMPLETED,
            started_at=started,
            completed_at=completed,
            source_counts=counts,
            records=records,
            stale_chunk_ids=sorted(set(stale_chunk_ids or [])),
        )
        save_manifest(stage / "manifest.json", manifest)
        if vector_source is not None:
            shutil.copytree(vector_source, stage / "vectors")
        backup = index_dir.with_name(f".{index_dir.name}.previous")
        if backup.exists():
            shutil.rmtree(backup)
        if index_dir.exists():
            os.replace(index_dir, backup)
        os.replace(stage, index_dir)
        if backup.exists():
            shutil.rmtree(backup)
    finally:
        if stage.exists():
            shutil.rmtree(stage)
