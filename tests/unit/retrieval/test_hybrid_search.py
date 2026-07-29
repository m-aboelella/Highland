from __future__ import annotations

from datetime import UTC, datetime

import pytest

from highland.models.scripted import (
    DeterministicEmbeddingModel,
    ScriptedRerankModel,
    ScriptedRerankStep,
)
from highland.retrieval.contracts import SourceDocument, chunk_document
from highland.retrieval.faiss_store import EmbeddingIndex
from highland.retrieval.hybrid import (
    HybridRetriever,
    LexicalIndex,
    RetrievalFilters,
    reciprocal_rank_fusion,
)


def make_chunk(source_id: str, text: str, customer_id: str = "cus_northwind"):
    return chunk_document(
        SourceDocument(
            source_system="relay",
            source_id=source_id,
            title=source_id,
            text=text,
            source_type="ticket",
            customer_id=customer_id,
            visibility="support",
            updated_at=datetime(2026, 7, 29, tzinfo=UTC),
            source_url=f"https://relay.test/{source_id}",
            metadata={"key": source_id.upper()},
        )
    )[0]


def test_exact_identifiers_are_discoverable_lexically() -> None:
    chunks = [
        make_chunk("sup-1042", "Production search is slow."),
        make_chunk("sup-1000", "A billing question."),
    ]
    assert LexicalIndex(chunks).search("SUP-1042", limit=2)[0][0] == chunks[0].id


def test_reciprocal_rank_fusion_preserves_both_discovery_paths() -> None:
    ordered, diagnostics = reciprocal_rank_fusion(
        [("lexical", 9), ("both", 2)],
        [("semantic", 0.9), ("both", 0.8)],
    )
    assert ordered[0] == "both"
    assert diagnostics["lexical"].lexical_rank == 1
    assert diagnostics["semantic"].semantic_rank == 1


@pytest.mark.asyncio
async def test_semantic_candidates_are_reranked_with_provenance(tmp_path) -> None:
    chunks = [
        make_chunk("sup-1042", "Compaction causes memory pressure."),
        make_chunk("sup-1000", "The renewal date is October."),
    ]
    embeddings = DeterministicEmbeddingModel(dimensions=16)
    index = EmbeddingIndex(embeddings)
    store = await index.build(chunks, tmp_path / "vectors")
    reranker = ScriptedRerankModel([ScriptedRerankStep(scores=(0.9, 0.1))])
    response = await HybridRetriever(
        chunks,
        vector_store=store,
        embedding_index=index,
        rerankers={"fast": reranker},
    ).search(
        "memory pressure",
        filters=RetrievalFilters(allowed_visibilities={"support"}),
    )
    assert response.results[0].chunk.source_url == "https://relay.test/sup-1042"
    assert response.rerank_usage.search_units == 1
    assert reranker.requests[0].documents[0].metadata["source_url"]
