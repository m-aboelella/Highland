from __future__ import annotations

import os
from datetime import UTC, datetime

import pytest

from highland.models.provider import build_model_provider
from highland.retrieval.contracts import SourceDocument, chunk_document
from highland.retrieval.faiss_store import EmbeddingIndex
from highland.settings import HighlandSettings, ModelBackend

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(
        os.getenv("HIGHLAND_RUN_LIVE_TESTS") != "1" or not os.getenv("COHERE_API_KEY"),
        reason="requires HIGHLAND_RUN_LIVE_TESTS=1 and COHERE_API_KEY",
    ),
]


@pytest.mark.asyncio
async def test_live_cohere_embedding_index_round_trip(tmp_path) -> None:
    provider = build_model_provider(HighlandSettings(model_backend=ModelBackend.COHERE))
    document = SourceDocument(
        source_system="archive",
        source_id="doc_live",
        title="Compaction runbook",
        text="Pause compaction when shard memory pressure causes retrieval latency.",
        source_type="runbook",
        visibility="support",
        updated_at=datetime.now(UTC),
        source_url="https://archive.test/doc_live",
    )
    chunks = chunk_document(document)
    index = EmbeddingIndex(provider.embeddings)
    store = await index.build(chunks, tmp_path / "vectors")
    matches = await index.query(store, "How should memory-related latency be mitigated?")
    assert matches[0].chunk_id == chunks[0].id
