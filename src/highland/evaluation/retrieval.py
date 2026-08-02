from __future__ import annotations

import hashlib
import importlib.metadata
import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal, Protocol

from pydantic import BaseModel, ConfigDict, Field

from highland.discover.service import DiscoverFilters, SearchRequest
from highland.retrieval.hybrid import RetrievalResponse
from highland.retrieval.sync import load_chunks


class EvaluationModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class RetrievalCase(EvaluationModel):
    id: str
    kind: Literal[
        "exact_id", "paraphrase", "filter", "stale_record", "no_answer", "customer_isolation"
    ]
    query: str
    relevant_source_ids: list[str] = Field(default_factory=list)
    filters: DiscoverFilters = Field(default_factory=DiscoverFilters)
    forbidden_customer_ids: list[str] = Field(default_factory=list)
    expect_no_results: bool = False


class RelevanceSet(EvaluationModel):
    version: int = 1
    reviewed: bool
    cases: list[RetrievalCase]


class RetrievalCaseResult(EvaluationModel):
    case_id: str
    kind: str
    candidate_source_ids: list[str]
    top_k_source_ids: list[str]
    missing_candidate_ids: list[str]
    missing_top_k_ids: list[str]
    leaked_source_ids: list[str]
    candidate_recall: float
    precision_at_k: float
    recall_at_k: float
    reciprocal_rank: float
    latency_ms: float
    passed: bool
    failure_stage: Literal["candidate", "rerank", "isolation", "no_answer"] | None = None


class RetrievalProvenance(EvaluationModel):
    backend: str
    embedding_model: str
    rerank_model: str
    cohere_sdk_version: str
    corpus_hash: str
    index_fingerprint: str
    configuration_hash: str
    relevance_set: str


class BaselineDelta(EvaluationModel):
    baseline_path: str
    candidate_recall: float
    precision_at_k: float
    recall_at_k: float
    mrr_at_k: float


class RetrievalBaseline(EvaluationModel):
    version: int = 1
    backend: str
    relevance_set_version: int
    candidate_recall: float
    precision_at_k: float
    recall_at_k: float
    mrr_at_k: float


class RetrievalEvaluation(EvaluationModel):
    generated_at: datetime
    provenance: RetrievalProvenance
    case_count: int
    candidate_recall: float
    precision_at_k: float
    recall_at_k: float
    mrr_at_k: float
    mean_latency_ms: float
    stage_loss: dict[str, int]
    passed: bool
    baseline_delta: BaselineDelta | None = None
    cases: list[RetrievalCaseResult]


class RetrievalSearcher(Protocol):
    async def search(self, request: SearchRequest) -> RetrievalResponse: ...


def load_relevance_set(path: Path) -> RelevanceSet:
    relevance = RelevanceSet.model_validate_json(path.read_text(encoding="utf-8"))
    if not relevance.reviewed:
        raise ValueError(f"retrieval relevance set is not marked reviewed: {path}")
    identities = [case.id for case in relevance.cases]
    if len(identities) != len(set(identities)):
        raise ValueError("retrieval relevance case IDs must be unique")
    return relevance


def _hash_files(paths: list[Path]) -> str:
    digest = hashlib.sha256()
    for path in sorted(paths):
        digest.update(path.name.encode())
        digest.update(path.read_bytes())
    return digest.hexdigest()


def _unique(values: list[str]) -> list[str]:
    return list(dict.fromkeys(values))


def _provenance(
    *,
    index_dir: Path,
    relevance_path: Path,
    backend: str,
    model_ids: dict[str, str],
) -> RetrievalProvenance:
    chunks_path = index_dir / "chunks.jsonl"
    index_files = [
        path
        for path in (
            chunks_path,
            index_dir / "manifest.json",
            index_dir / "vectors" / "vector-metadata.json",
            index_dir / "vectors" / "vector-chunks.json",
            index_dir / "vectors" / "vectors.faiss",
        )
        if path.exists()
    ]
    if not chunks_path.exists():
        raise FileNotFoundError("Search index is missing; run `highland index rebuild` first")
    corpus_hash = hashlib.sha256(chunks_path.read_bytes()).hexdigest()
    index_fingerprint = _hash_files(index_files)
    configuration = {
        "backend": backend,
        "embedding_model": model_ids["embedding"],
        "rerank_model": model_ids["rerank"],
        "index_fingerprint": index_fingerprint,
        "relevance_sha256": hashlib.sha256(relevance_path.read_bytes()).hexdigest(),
    }
    return RetrievalProvenance(
        backend=backend,
        embedding_model=model_ids["embedding"],
        rerank_model=model_ids["rerank"],
        cohere_sdk_version=importlib.metadata.version("cohere"),
        corpus_hash=corpus_hash,
        index_fingerprint=index_fingerprint,
        configuration_hash=hashlib.sha256(
            json.dumps(configuration, sort_keys=True).encode()
        ).hexdigest(),
        relevance_set=str(relevance_path),
    )


def _baseline_delta(report: RetrievalEvaluation, baseline_path: Path) -> BaselineDelta:
    baseline = RetrievalBaseline.model_validate_json(baseline_path.read_text(encoding="utf-8"))
    return BaselineDelta(
        baseline_path=str(baseline_path),
        candidate_recall=report.candidate_recall - baseline.candidate_recall,
        precision_at_k=report.precision_at_k - baseline.precision_at_k,
        recall_at_k=report.recall_at_k - baseline.recall_at_k,
        mrr_at_k=report.mrr_at_k - baseline.mrr_at_k,
    )


async def evaluate_retrieval(
    searcher: RetrievalSearcher,
    *,
    relevance_path: Path,
    index_dir: Path,
    reports_dir: Path,
    backend: str,
    model_ids: dict[str, str],
    baseline_path: Path | None = None,
    enforce_baseline: bool = False,
) -> RetrievalEvaluation:
    relevance = load_relevance_set(relevance_path)
    chunks = {chunk.id: chunk for chunk in load_chunks(index_dir)}
    provenance = _provenance(
        index_dir=index_dir,
        relevance_path=relevance_path,
        backend=backend,
        model_ids=model_ids,
    )
    results: list[RetrievalCaseResult] = []
    stage_loss = {"candidate": 0, "rerank": 0, "isolation": 0, "no_answer": 0}
    for case in relevance.cases:
        response = await searcher.search(SearchRequest(query=case.query, filters=case.filters))
        candidate_chunks = [
            chunks[item.chunk_id]
            for item in response.diagnostics
            if item.filter_allowed and item.chunk_id in chunks
        ]
        candidate_ids = _unique([chunk.source_id for chunk in candidate_chunks])
        top_ids = _unique([item.chunk.source_id for item in response.results])
        relevant = set(case.relevant_source_ids)
        missing_candidate = sorted(relevant - set(candidate_ids))
        missing_top = sorted(relevant - set(top_ids))
        forbidden = set(case.forbidden_customer_ids)
        leaked = _unique(
            [chunk.source_id for chunk in candidate_chunks if chunk.customer_id in forbidden]
            + [
                item.chunk.source_id
                for item in response.results
                if item.chunk.customer_id in forbidden
            ]
        )
        no_answer_failure = case.expect_no_results and bool(top_ids)
        failure_stage = (
            "isolation"
            if leaked
            else "no_answer"
            if no_answer_failure
            else "candidate"
            if missing_candidate
            else "rerank"
            if missing_top
            else None
        )
        if failure_stage:
            stage_loss[failure_stage] += 1
        candidate_recall = (
            len(relevant & set(candidate_ids)) / len(relevant) if relevant else 1.0
        )
        precision = len(relevant & set(top_ids)) / len(top_ids) if top_ids else float(not relevant)
        recall = len(relevant & set(top_ids)) / len(relevant) if relevant else float(not top_ids)
        ranks = [index for index, value in enumerate(top_ids, start=1) if value in relevant]
        reciprocal_rank = 1 / min(ranks) if ranks else float(not relevant and not top_ids)
        results.append(
            RetrievalCaseResult(
                case_id=case.id,
                kind=case.kind,
                candidate_source_ids=candidate_ids,
                top_k_source_ids=top_ids,
                missing_candidate_ids=missing_candidate,
                missing_top_k_ids=missing_top,
                leaked_source_ids=leaked,
                candidate_recall=candidate_recall,
                precision_at_k=precision,
                recall_at_k=recall,
                reciprocal_rank=reciprocal_rank,
                latency_ms=response.timings.total_ms,
                passed=failure_stage is None,
                failure_stage=failure_stage,
            )
        )
    count = len(results)
    report = RetrievalEvaluation(
        generated_at=datetime.now(UTC),
        provenance=provenance,
        case_count=count,
        candidate_recall=sum(item.candidate_recall for item in results) / count,
        precision_at_k=sum(item.precision_at_k for item in results) / count,
        recall_at_k=sum(item.recall_at_k for item in results) / count,
        mrr_at_k=sum(item.reciprocal_rank for item in results) / count,
        mean_latency_ms=sum(item.latency_ms for item in results) / count,
        stage_loss=stage_loss,
        passed=all(item.passed for item in results),
        cases=results,
    )
    if baseline_path:
        delta = _baseline_delta(report, baseline_path)
        baseline_passed = all(
            value >= -1e-12
            for value in (
                delta.candidate_recall,
                delta.precision_at_k,
                delta.recall_at_k,
                delta.mrr_at_k,
            )
        )
        report = report.model_copy(
            update={
                "baseline_delta": delta,
                "passed": report.passed and (baseline_passed or not enforce_baseline),
            }
        )
    reports_dir.mkdir(parents=True, exist_ok=True)
    (reports_dir / "retrieval.json").write_text(
        report.model_dump_json(indent=2) + "\n", encoding="utf-8"
    )
    lines = [
        "# Retrieval evaluation",
        "",
        f"- Backend: `{report.provenance.backend}`",
        f"- Embedding model: `{report.provenance.embedding_model}`",
        f"- Rerank model: `{report.provenance.rerank_model}`",
        f"- Candidate recall: {report.candidate_recall:.1%}",
        f"- Precision@k: {report.precision_at_k:.1%}",
        f"- Recall@k: {report.recall_at_k:.1%}",
        f"- MRR@k: {report.mrr_at_k:.3f}",
        f"- Mean latency: {report.mean_latency_ms:.2f} ms",
        f"- Result: {'PASS' if report.passed else 'FAIL'}",
        "",
        "| Case | Kind | Result | Lost stage | Missing evidence | Leakage |",
        "| --- | --- | --- | --- | --- | --- |",
    ]
    for item in results:
        lines.append(
            f"| {item.case_id} | {item.kind} | {'PASS' if item.passed else 'FAIL'} "
            f"| {item.failure_stage or '—'} | {', '.join(item.missing_top_k_ids) or '—'} "
            f"| {', '.join(item.leaked_source_ids) or '—'} |"
        )
    if report.baseline_delta:
        lines.extend(
            [
                "",
                "## Baseline delta",
                "",
                f"- Candidate recall: {report.baseline_delta.candidate_recall:+.3f}",
                f"- Precision@k: {report.baseline_delta.precision_at_k:+.3f}",
                f"- Recall@k: {report.baseline_delta.recall_at_k:+.3f}",
                f"- MRR@k: {report.baseline_delta.mrr_at_k:+.3f}",
            ]
        )
    (reports_dir / "retrieval.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    return report
