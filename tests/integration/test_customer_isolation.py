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
from highland.retrieval.hybrid import HybridRetriever, RetrievalFilters


def make_chunk(source_id: str, text: str, customer_id: str):
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
        )
    )[0]


@pytest.mark.asyncio
async def test_other_customer_text_never_reaches_rerank(tmp_path) -> None:
    chunks = [
        make_chunk("sup-northwind", "Shared latency phrase.", "cus_northwind"),
        make_chunk("sup-contoso", "Shared latency phrase secret.", "cus_contoso"),
    ]
    embeddings = DeterministicEmbeddingModel(dimensions=16)
    index = EmbeddingIndex(embeddings)
    store = await index.build(chunks, tmp_path / "vectors")
    reranker = ScriptedRerankModel([ScriptedRerankStep(scores=(0.9,))])
    response = await HybridRetriever(
        chunks,
        vector_store=store,
        embedding_index=index,
        reranker=reranker,
    ).search(
        "Shared latency phrase",
        filters=RetrievalFilters(
            customer_id="cus_northwind",
            allowed_visibilities={"support"},
        ),
    )
    sent = reranker.requests[0].documents
    assert [document.id for document in sent] == [chunks[0].id]
    assert "secret" not in sent[0].text
    rejected = next(item for item in response.diagnostics if item.chunk_id == chunks[1].id)
    assert rejected.filter_reason == "customer"
