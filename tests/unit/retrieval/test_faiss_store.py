from __future__ import annotations

from datetime import UTC, datetime

import pytest

from highland.models.contracts import InputType
from highland.models.scripted import DeterministicEmbeddingModel, ScriptedFailure
from highland.retrieval.contracts import SourceDocument, chunk_document
from highland.retrieval.faiss_store import (
    EmbeddingIndex,
    FaissStore,
    IncompatibleVectorIndex,
    VectorIndexError,
)


def chunks():
    return [
        chunk_document(
            SourceDocument(
                source_system="archive",
                source_id=f"doc_{index}",
                title=title,
                text=text,
                source_type="runbook",
                visibility="support",
                updated_at=datetime(2026, 7, 29, tzinfo=UTC),
                source_url=f"https://archive.test/doc_{index}",
            )
        )[0]
        for index, (title, text) in enumerate(
            [
                ("Latency", "Compaction raises retrieval latency."),
                ("Renewal", "The annual renewal is due in October."),
            ]
        )
    ]


@pytest.mark.asyncio
async def test_close_and_reopen_preserves_nearest_neighbors(tmp_path) -> None:
    model = DeterministicEmbeddingModel(dimensions=16, model="embed-test")
    index = EmbeddingIndex(model, batch_size=1)
    store = await index.build(chunks(), tmp_path / "vectors")
    before = await index.query(store, "Compaction raises retrieval latency.", limit=2)
    reopened = FaissStore.open(
        tmp_path / "vectors", expected_model_id="embed-test", expected_dimension=16
    )
    after = await index.query(reopened, "Compaction raises retrieval latency.", limit=2)
    assert before == after
    assert before[0].chunk_id == chunks()[0].id
    assert [request.input_type for request in model.requests[:2]] == [
        InputType.SEARCH_DOCUMENT,
        InputType.SEARCH_DOCUMENT,
    ]
    assert model.requests[-1].input_type is InputType.SEARCH_QUERY


@pytest.mark.asyncio
async def test_model_or_dimension_change_requires_rebuild(tmp_path) -> None:
    await EmbeddingIndex(DeterministicEmbeddingModel(dimensions=8, model="embed-a")).build(
        chunks(), tmp_path / "vectors"
    )
    with pytest.raises(IncompatibleVectorIndex, match="rebuild"):
        FaissStore.open(tmp_path / "vectors", expected_model_id="embed-b")
    with pytest.raises(IncompatibleVectorIndex, match="rebuild"):
        FaissStore.open(tmp_path / "vectors", expected_dimension=16)


def test_empty_and_invalid_vectors_have_explicit_behavior(tmp_path) -> None:
    store = FaissStore(tmp_path / "vectors", model_id="embed-a", dimension=3)
    store.add([], [])
    assert store.search([1, 0, 0]) == []
    with pytest.raises(VectorIndexError, match="counts differ"):
        store.add(["chunk"], [])
    with pytest.raises(IncompatibleVectorIndex, match="expected 3"):
        store.add(["chunk"], [[1, 2]])


@pytest.mark.asyncio
async def test_failed_embedding_does_not_replace_existing_index(tmp_path) -> None:
    path = tmp_path / "vectors"
    healthy = EmbeddingIndex(DeterministicEmbeddingModel(dimensions=8, model="embed-a"))
    await healthy.build(chunks(), path)
    previous = (path / FaissStore.MAPPING_FILE).read_bytes()

    class FailingModel:
        name = "embed-a"
        model = "embed-a"

        async def embed(self, request):
            raise RuntimeError(ScriptedFailure.timeout().message)

    with pytest.raises(RuntimeError, match="timeout"):
        await EmbeddingIndex(FailingModel()).build(chunks(), path)
    assert (path / FaissStore.MAPPING_FILE).read_bytes() == previous
