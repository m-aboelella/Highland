"""Local ingestion, indexing, retrieval, and citation support."""

from .contracts import (
    Chunk,
    ChunkLocation,
    SourceDocument,
    SyncManifest,
    SyncRecord,
    SyncState,
    chunk_document,
)

__all__ = [
    "Chunk",
    "ChunkLocation",
    "SourceDocument",
    "SyncManifest",
    "SyncRecord",
    "SyncState",
    "chunk_document",
]
