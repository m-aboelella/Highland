from __future__ import annotations

import json
import re
from enum import StrEnum
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field

from highland.models.contracts import Citation

from .contracts import Chunk, SyncState
from .manifest import load_manifest

_CHUNK_ID = re.compile(r"^chk_[a-f0-9]{24}$")


class CitationStatus(StrEnum):
    RESOLVED = "resolved"
    STALE = "stale"
    UNRESOLVED = "unresolved"


class CitationSource(BaseModel):
    model_config = ConfigDict(extra="forbid")
    chunk_id: str
    status: CitationStatus
    text: str | None = None
    source_system: str | None = None
    source_id: str | None = None
    title: str | None = None
    section: str | None = None
    passage_id: str | None = None
    source_url: str | None = None


class ResolvedCitation(BaseModel):
    model_config = ConfigDict(extra="forbid")
    start: int = Field(ge=0)
    end: int = Field(ge=0)
    text: str
    sources: list[CitationSource]


class CitationResolver:
    def __init__(self, index_dir: Path) -> None:
        self.index_dir = index_dir.resolve()
        self._chunks = self._load_chunks()
        manifest = load_manifest(self.index_dir / "manifest.json")
        stale = set(manifest.stale_chunk_ids if manifest else [])
        if manifest:
            stale.update(
                chunk_id
                for record in manifest.records.values()
                if record.state is SyncState.TOMBSTONED
                for chunk_id in record.chunk_ids
            )
        self._stale = stale

    def _load_chunks(self) -> dict[str, Chunk]:
        path = self.index_dir / "chunks.jsonl"
        if not path.is_file():
            return {}
        return {
            chunk.id: chunk
            for chunk in (
                Chunk.model_validate(json.loads(line))
                for line in path.read_text(encoding="utf-8").splitlines()
                if line.strip()
            )
        }

    def get_chunk(self, chunk_id: str) -> Chunk | None:
        if not _CHUNK_ID.fullmatch(chunk_id):
            return None
        return self._chunks.get(chunk_id)

    def resolve(self, citation: Citation) -> ResolvedCitation:
        sources: list[CitationSource] = []
        for chunk_id in citation.source_ids:
            chunk = self.get_chunk(chunk_id)
            if chunk:
                sources.append(
                    CitationSource(
                        chunk_id=chunk_id,
                        status=CitationStatus.RESOLVED,
                        text=chunk.text,
                        source_system=chunk.source_system,
                        source_id=chunk.source_id,
                        title=chunk.title,
                        section=chunk.location.section,
                        passage_id=chunk.location.passage_id,
                        source_url=chunk.source_url,
                    )
                )
            elif chunk_id in self._stale:
                sources.append(CitationSource(chunk_id=chunk_id, status=CitationStatus.STALE))
            else:
                sources.append(CitationSource(chunk_id=chunk_id, status=CitationStatus.UNRESOLVED))
        if not sources:
            sources.append(CitationSource(chunk_id="", status=CitationStatus.UNRESOLVED))
        return ResolvedCitation(
            start=citation.start,
            end=citation.end,
            text=citation.text,
            sources=sources,
        )
