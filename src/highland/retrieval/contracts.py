from __future__ import annotations

import hashlib
import re
from datetime import UTC, datetime
from enum import StrEnum
from typing import Self

from pydantic import BaseModel, ConfigDict, Field, model_validator


class RetrievalModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ChunkLocation(RetrievalModel):
    section: str
    ordinal: int = Field(ge=0)
    passage_id: str | None = None


class SourceDocument(RetrievalModel):
    source_system: str
    source_id: str
    title: str
    text: str
    source_type: str
    visibility: str
    updated_at: datetime
    source_url: str
    customer_id: str | None = None
    section: str = "Content"
    passage_id: str | None = None
    metadata: dict[str, str | int | float | bool | None] = Field(default_factory=dict)

    @property
    def content_hash(self) -> str:
        return hashlib.sha256(self.text.encode("utf-8")).hexdigest()


class Chunk(RetrievalModel):
    id: str
    source_system: str
    source_id: str
    title: str
    text: str
    content_hash: str
    source_type: str
    visibility: str
    updated_at: datetime
    source_url: str
    location: ChunkLocation
    customer_id: str | None = None
    metadata: dict[str, str | int | float | bool | None] = Field(default_factory=dict)

    @classmethod
    def from_document(
        cls,
        document: SourceDocument,
        *,
        text: str,
        ordinal: int,
        section: str,
        passage_id: str | None = None,
    ) -> Self:
        content_hash = hashlib.sha256(text.encode("utf-8")).hexdigest()
        identity = f"{document.source_system}:{document.source_id}:{content_hash}:{ordinal}"
        chunk_id = f"chk_{hashlib.sha256(identity.encode()).hexdigest()[:24]}"
        source_url = document.source_url
        if passage_id and "#" not in source_url:
            source_url = f"{source_url}#passage={passage_id}"
        return cls(
            id=chunk_id,
            source_system=document.source_system,
            source_id=document.source_id,
            title=document.title,
            text=text,
            content_hash=content_hash,
            source_type=document.source_type,
            visibility=document.visibility,
            updated_at=document.updated_at,
            source_url=source_url,
            location=ChunkLocation(
                section=section,
                ordinal=ordinal,
                passage_id=passage_id or document.passage_id,
            ),
            customer_id=document.customer_id,
            metadata=document.metadata,
        )


class SyncState(StrEnum):
    COMPLETED = "completed"
    PARTIAL = "partial"
    FAILED = "failed"
    TOMBSTONED = "tombstoned"


class SyncRecord(RetrievalModel):
    source_system: str
    source_id: str
    content_hash: str
    updated_at: datetime
    chunk_ids: list[str]
    state: SyncState = SyncState.COMPLETED
    error: str | None = None

    @model_validator(mode="after")
    def validate_state(self) -> Self:
        if self.state is SyncState.FAILED and not self.error:
            raise ValueError("failed sync records require an error")
        return self


class SyncManifest(RetrievalModel):
    version: int = 1
    state: SyncState
    started_at: datetime
    completed_at: datetime | None = None
    records: dict[str, SyncRecord] = Field(default_factory=dict)
    source_counts: dict[str, int] = Field(default_factory=dict)
    source_cursors: dict[str, str | None] = Field(default_factory=dict)
    failures: dict[str, str] = Field(default_factory=dict)

    @classmethod
    def start(cls) -> Self:
        return cls(state=SyncState.PARTIAL, started_at=datetime.now(UTC))


_HEADING = re.compile(r"(?m)^#{1,6}\s+(.+?)\s*$")
_PARAGRAPHS = re.compile(r"\n\s*\n")


def _semantic_sections(document: SourceDocument) -> list[tuple[str, str]]:
    """Split at explicit source passages/headings, then paragraph boundaries."""
    if document.passage_id:
        return [(document.section, document.text.strip())]
    matches = list(_HEADING.finditer(document.text))
    if matches:
        sections: list[tuple[str, str]] = []
        for index, match in enumerate(matches):
            start = match.end()
            end = matches[index + 1].start() if index + 1 < len(matches) else len(document.text)
            body = document.text[start:end].strip()
            if body:
                sections.append((match.group(1), body))
        if sections:
            return sections
    paragraphs = [part.strip() for part in _PARAGRAPHS.split(document.text) if part.strip()]
    return [(document.section, paragraph) for paragraph in paragraphs] or [
        (document.section, document.text.strip())
    ]


def _bounded_parts(text: str, *, max_chars: int) -> list[str]:
    if len(text) <= max_chars:
        return [text]
    sentences = re.split(r"(?<=[.!?])\s+", text)
    parts: list[str] = []
    current = ""
    for sentence in sentences:
        if len(sentence) > max_chars:
            if current:
                parts.append(current)
                current = ""
            parts.extend(
                sentence[start : start + max_chars] for start in range(0, len(sentence), max_chars)
            )
        elif not current:
            current = sentence
        elif len(current) + 1 + len(sentence) <= max_chars:
            current = f"{current} {sentence}"
        else:
            parts.append(current)
            current = sentence
    if current:
        parts.append(current)
    return parts


def chunk_document(document: SourceDocument, *, max_chars: int = 1800) -> list[Chunk]:
    if max_chars < 100:
        raise ValueError("max_chars must be at least 100")
    chunks: list[Chunk] = []
    for section, body in _semantic_sections(document):
        for part in _bounded_parts(body, max_chars=max_chars):
            if not part:
                continue
            chunks.append(
                Chunk.from_document(
                    document,
                    text=part,
                    ordinal=len(chunks),
                    section=section,
                    passage_id=document.passage_id,
                )
            )
    return chunks
