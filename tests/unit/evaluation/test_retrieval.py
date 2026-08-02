from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

import pytest

from highland.cli import _retrieval_baseline_path
from highland.discover.service import SearchRequest
from highland.evaluation.retrieval import evaluate_retrieval, load_relevance_set
from highland.models.scripted import DeterministicEmbeddingModel, DeterministicRerankModel
from highland.retrieval.contracts import SourceDocument, chunk_document
from highland.retrieval.faiss_store import EmbeddingIndex, FaissStore
from highland.retrieval.hybrid import HybridRetriever
from highland.retrieval.ingestion import promote_snapshot
from highland.settings import HighlandSettings


class ProductionSearcher:
    def __init__(self, retriever: HybridRetriever) -> None:
        self.retriever = retriever

    async def search(self, request: SearchRequest):  # type: ignore[no-untyped-def]
        return await self.retriever.search(request.query, filters=request.filters.retrieval())


def _write_relevance(path: Path, cases: list[dict[str, object]]) -> None:
    path.write_text(json.dumps({"version": 1, "reviewed": True, "cases": cases}))


def test_retrieval_artifact_defaults_resolve_to_checked_in_files() -> None:
    settings = HighlandSettings()

    assert settings.retrieval_relevance_config.name == "retrieval-relevance.json"
    assert settings.retrieval_baseline_config.name == "retrieval-baseline.json"
    assert settings.retrieval_relevance_config.is_file()
    assert settings.retrieval_baseline_config.is_file()


def test_retrieval_baseline_selection_preserves_cli_semantics(tmp_path: Path) -> None:
    configured = tmp_path / "configured.json"
    requested = tmp_path / "requested.json"

    assert _retrieval_baseline_path(configured, None, enforce=False) is None
    assert _retrieval_baseline_path(configured, None, enforce=True) == configured
    assert _retrieval_baseline_path(configured, requested, enforce=False) == requested


async def _searcher(index_dir: Path) -> ProductionSearcher:
    now = datetime(2026, 7, 29, tzinfo=UTC)
    documents = [
        SourceDocument(
            source_system="relay",
            source_id="tkt_1",
            title="SUP-1: Production latency",
            text="Shard memory pressure during compaction increased retrieval latency.",
            source_type="ticket",
            visibility="support",
            updated_at=now,
            source_url="http://relay/tickets/tkt_1",
            customer_id="cus_a",
        ),
        SourceDocument(
            source_system="relay",
            source_id="foreign",
            title="Other customer latency",
            text="Production latency and memory pressure.",
            source_type="ticket",
            visibility="support",
            updated_at=now,
            source_url="http://relay/tickets/foreign",
            customer_id="cus_b",
        ),
    ]
    chunks = [chunk for document in documents for chunk in chunk_document(document)]
    embeddings = DeterministicEmbeddingModel()
    await EmbeddingIndex(embeddings).build(chunks, index_dir / "vectors")
    promote_snapshot(
        index_dir,
        chunks=chunks,
        documents=documents,
        started=now,
        completed=now,
        counts={"relay": 2},
        vector_source=index_dir / "vectors",
    )
    # promote_snapshot replaces index_dir, so rebuild vectors in the promoted location.
    await EmbeddingIndex(embeddings).build(chunks, index_dir / "vectors")
    return ProductionSearcher(
        HybridRetriever(
            chunks,
            vector_store=FaissStore.open(
                index_dir / "vectors", expected_model_id=embeddings.model
            ),
            embedding_index=EmbeddingIndex(embeddings),
            reranker=DeterministicRerankModel(),
            candidate_limit=10,
            result_limit=5,
        )
    )


@pytest.mark.asyncio
async def test_retrieval_uses_production_results_and_writes_standard_metrics(
    tmp_path: Path,
) -> None:
    relevance = tmp_path / "relevance.json"
    _write_relevance(
        relevance,
        [
            {
                "id": "exact",
                "kind": "exact_id",
                "query": "SUP-1 production latency",
                "relevant_source_ids": ["tkt_1"],
                "filters": {"customer_id": "cus_a"},
                "forbidden_customer_ids": ["cus_b"],
            }
        ],
    )
    index = tmp_path / "index"
    report = await evaluate_retrieval(
        await _searcher(index),
        relevance_path=relevance,
        index_dir=index,
        reports_dir=tmp_path / "reports",
        backend="scripted",
        model_ids={"embedding": "deterministic-embedding", "rerank": "deterministic-rerank"},
    )
    assert report.passed
    assert report.candidate_recall == 1
    assert report.recall_at_k == 1
    assert report.mrr_at_k == 1
    assert report.cases[0].candidate_source_ids == ["tkt_1"]
    assert report.cases[0].leaked_source_ids == []
    assert report.provenance.index_fingerprint
    assert (tmp_path / "reports" / "retrieval.json").exists()
    assert "Precision@k" in (tmp_path / "reports" / "retrieval.md").read_text()


@pytest.mark.asyncio
async def test_baseline_enforcement_fails_metric_regression(tmp_path: Path) -> None:
    index = tmp_path / "index"
    searcher = await _searcher(index)
    good = tmp_path / "good.json"
    _write_relevance(
        good,
        [
            {
                "id": "good",
                "kind": "exact_id",
                "query": "SUP-1",
                "relevant_source_ids": ["tkt_1"],
                "filters": {"customer_id": "cus_a"},
            }
        ],
    )
    baseline_report = await evaluate_retrieval(
        searcher,
        relevance_path=good,
        index_dir=index,
        reports_dir=tmp_path / "baseline-report",
        backend="scripted",
        model_ids={"embedding": "deterministic-embedding", "rerank": "deterministic-rerank"},
    )
    baseline = tmp_path / "baseline.json"
    baseline.write_text(
        json.dumps(
            {
                "version": 1,
                "backend": "scripted",
                "relevance_set_version": 1,
                "candidate_recall": baseline_report.candidate_recall,
                "precision_at_k": baseline_report.precision_at_k,
                "recall_at_k": baseline_report.recall_at_k,
                "mrr_at_k": baseline_report.mrr_at_k,
            }
        )
    )
    regressed = tmp_path / "regressed.json"
    _write_relevance(
        regressed,
        [
            {
                "id": "miss",
                "kind": "paraphrase",
                "query": "SUP-1",
                "relevant_source_ids": ["missing"],
            }
        ],
    )
    report = await evaluate_retrieval(
        searcher,
        relevance_path=regressed,
        index_dir=index,
        reports_dir=tmp_path / "regressed-report",
        backend="scripted",
        model_ids={"embedding": "deterministic-embedding", "rerank": "deterministic-rerank"},
        baseline_path=baseline,
        enforce_baseline=True,
    )
    assert not report.passed
    assert report.baseline_delta is not None
    assert report.baseline_delta.recall_at_k < 0


def test_relevance_set_must_be_reviewed(tmp_path: Path) -> None:
    path = tmp_path / "relevance.json"
    path.write_text('{"version":1,"reviewed":false,"cases":[]}')
    with pytest.raises(ValueError, match="not marked reviewed"):
        load_relevance_set(path)
