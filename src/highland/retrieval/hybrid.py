from __future__ import annotations

import math
import re
import time
from collections.abc import Mapping
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from highland.models.contracts import Document, RerankModel, RerankRequest, Usage

from .contracts import Chunk
from .faiss_store import EmbeddingIndex, FaissStore

_TOKEN = re.compile(r"[a-z0-9][a-z0-9._-]*", re.IGNORECASE)


class RetrievalFilters(BaseModel):
    model_config = ConfigDict(extra="forbid")
    customer_id: str | None = None
    source_types: set[str] = Field(default_factory=set)
    allowed_visibilities: set[str] = Field(default_factory=set)
    updated_after: datetime | None = None
    updated_before: datetime | None = None


class CandidateDiagnostics(BaseModel):
    model_config = ConfigDict(extra="forbid")
    chunk_id: str
    lexical_rank: int | None = None
    semantic_rank: int | None = None
    fused_score: float = 0
    filter_allowed: bool = True
    filter_reason: str | None = None
    rerank_score: float | None = None


class RetrievalTimings(BaseModel):
    model_config = ConfigDict(extra="forbid")
    lexical_ms: float = Field(ge=0)
    semantic_ms: float = Field(ge=0)
    filter_ms: float = Field(ge=0)
    rerank_ms: float = Field(ge=0)
    total_ms: float = Field(ge=0)


class RetrievalResult(BaseModel):
    model_config = ConfigDict(extra="forbid")
    chunk: Chunk
    score: float


class RetrievalResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")
    query: str
    results: list[RetrievalResult]
    diagnostics: list[CandidateDiagnostics]
    timings: RetrievalTimings
    rerank_usage: Usage = Field(default_factory=Usage)
    rerank_model: str | None = None


def _tokens(text: str) -> list[str]:
    return [token.lower() for token in _TOKEN.findall(text)]


class LexicalIndex:
    """Small BM25-style scorer kept local so identifiers remain discoverable."""

    def __init__(self, chunks: list[Chunk]) -> None:
        self.chunks = chunks
        self.terms = [
            _tokens(
                " ".join(
                    (
                        chunk.title,
                        chunk.text,
                        chunk.source_id,
                        str(chunk.metadata.get("key", "")),
                    )
                )
            )
            for chunk in chunks
        ]
        self.average_length = sum(map(len, self.terms)) / len(self.terms) if self.terms else 1
        self.document_frequency: dict[str, int] = {}
        for terms in self.terms:
            for term in set(terms):
                self.document_frequency[term] = self.document_frequency.get(term, 0) + 1

    def search(self, query: str, *, limit: int) -> list[tuple[str, float]]:
        query_terms = _tokens(query)
        if not query_terms or not self.chunks:
            return []
        scores: list[tuple[str, float]] = []
        for chunk, terms in zip(self.chunks, self.terms, strict=True):
            score = 0.0
            length_ratio = len(terms) / self.average_length
            for term in query_terms:
                frequency = terms.count(term)
                if not frequency:
                    continue
                frequency_weight = (
                    frequency * 2.2 / (frequency + 1.2 * (0.25 + 0.75 * length_ratio))
                )
                inverse_frequency = math.log(
                    1
                    + (len(self.chunks) - self.document_frequency[term] + 0.5)
                    / (self.document_frequency[term] + 0.5)
                )
                score += frequency_weight * inverse_frequency
            searchable = f"{chunk.source_id} {chunk.metadata.get('key', '')}".lower()
            if query.lower().strip() in searchable:
                score += 5
            if score:
                scores.append((chunk.id, score))
        return sorted(scores, key=lambda item: (-item[1], item[0]))[:limit]


def reciprocal_rank_fusion(
    lexical: list[tuple[str, float]],
    semantic: list[tuple[str, float]],
    *,
    rank_constant: int = 60,
) -> tuple[list[str], dict[str, CandidateDiagnostics]]:
    diagnostics: dict[str, CandidateDiagnostics] = {}
    for name, ranking in (("lexical", lexical), ("semantic", semantic)):
        for rank, (chunk_id, _score) in enumerate(ranking, start=1):
            item = diagnostics.setdefault(chunk_id, CandidateDiagnostics(chunk_id=chunk_id))
            setattr(item, f"{name}_rank", rank)
            item.fused_score += 1 / (rank_constant + rank)
    ordered = sorted(
        diagnostics,
        key=lambda chunk_id: (-diagnostics[chunk_id].fused_score, chunk_id),
    )
    return ordered, diagnostics


def _filter(chunk: Chunk, filters: RetrievalFilters) -> str | None:
    if filters.customer_id is not None and chunk.customer_id not in (
        None,
        filters.customer_id,
    ):
        return "customer"
    if filters.source_types and chunk.source_type not in filters.source_types:
        return "source_type"
    if filters.allowed_visibilities and chunk.visibility not in filters.allowed_visibilities:
        return "visibility"
    if filters.updated_after and chunk.updated_at < filters.updated_after:
        return "updated_after"
    if filters.updated_before and chunk.updated_at > filters.updated_before:
        return "updated_before"
    return None


class HybridRetriever:
    def __init__(
        self,
        chunks: list[Chunk],
        *,
        vector_store: FaissStore,
        embedding_index: EmbeddingIndex,
        rerankers: Mapping[Literal["fast", "pro"], RerankModel],
        candidate_limit: int = 30,
        result_limit: int = 8,
    ) -> None:
        if candidate_limit <= 0 or result_limit <= 0:
            raise ValueError("retrieval limits must be positive")
        self.chunks = {chunk.id: chunk for chunk in chunks}
        self.lexical = LexicalIndex(chunks)
        self.vector_store = vector_store
        self.embedding_index = embedding_index
        self.rerankers = rerankers
        self.candidate_limit = candidate_limit
        self.result_limit = result_limit

    async def search(
        self,
        query: str,
        *,
        filters: RetrievalFilters,
        rerank_profile: Literal["fast", "pro"] = "fast",
    ) -> RetrievalResponse:
        if not query.strip():
            raise ValueError("retrieval query cannot be empty")
        started = time.perf_counter()
        lexical_started = time.perf_counter()
        lexical = self.lexical.search(query, limit=self.candidate_limit)
        lexical_ms = (time.perf_counter() - lexical_started) * 1000
        semantic_started = time.perf_counter()
        semantic_matches = await self.embedding_index.query(
            self.vector_store, query, limit=self.candidate_limit
        )
        semantic = [(match.chunk_id, match.score) for match in semantic_matches]
        semantic_ms = (time.perf_counter() - semantic_started) * 1000
        ordered, diagnostics = reciprocal_rank_fusion(lexical, semantic)

        filter_started = time.perf_counter()
        eligible: list[Chunk] = []
        for chunk_id in ordered[: self.candidate_limit]:
            chunk = self.chunks.get(chunk_id)
            diagnostic = diagnostics[chunk_id]
            if chunk is None:
                diagnostic.filter_allowed = False
                diagnostic.filter_reason = "stale_vector"
                continue
            reason = _filter(chunk, filters)
            if reason:
                diagnostic.filter_allowed = False
                diagnostic.filter_reason = reason
            else:
                eligible.append(chunk)
        filter_ms = (time.perf_counter() - filter_started) * 1000

        rerank_ms = 0.0
        usage = Usage()
        model_id: str | None = None
        results: list[RetrievalResult] = []
        if eligible:
            if rerank_profile not in self.rerankers:
                raise ValueError(f"rerank profile {rerank_profile!r} is not configured")
            reranker = self.rerankers[rerank_profile]
            rerank_started = time.perf_counter()
            response = await reranker.rerank(
                RerankRequest(
                    query=query,
                    documents=[
                        Document(
                            id=chunk.id,
                            text=chunk.text,
                            metadata={
                                "source_system": chunk.source_system,
                                "source_id": chunk.source_id,
                                "source_url": chunk.source_url,
                                "section": chunk.location.section,
                            },
                        )
                        for chunk in eligible
                    ],
                    top_n=min(self.result_limit, len(eligible)),
                )
            )
            rerank_ms = (time.perf_counter() - rerank_started) * 1000
            usage = response.usage
            model_id = response.metadata.model
            for ranked in response.results:
                chunk = self.chunks[ranked.document.id]
                diagnostics[chunk.id].rerank_score = ranked.relevance_score
                results.append(RetrievalResult(chunk=chunk, score=ranked.relevance_score))
        total_ms = (time.perf_counter() - started) * 1000
        return RetrievalResponse(
            query=query,
            results=results,
            diagnostics=[diagnostics[chunk_id] for chunk_id in ordered],
            timings=RetrievalTimings(
                lexical_ms=lexical_ms,
                semantic_ms=semantic_ms,
                filter_ms=filter_ms,
                rerank_ms=rerank_ms,
                total_ms=total_ms,
            ),
            rerank_usage=usage,
            rerank_model=model_id,
        )
