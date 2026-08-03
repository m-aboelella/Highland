from __future__ import annotations

from datetime import datetime
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field

from highland.models.contracts import Document, Message, MessageRole, Usage
from highland.models.provider import ModelProvider
from highland.retrieval.faiss_store import EmbeddingIndex, FaissStore, VectorIndexError
from highland.retrieval.hybrid import (
    CandidateDiagnostics,
    HybridRetriever,
    RetrievalFilters,
    RetrievalResponse,
    RetrievalResult,
    RetrievalTimings,
)
from highland.retrieval.sync import load_chunks
from highland.runtime.agent import AgentLoop, AgentProfile, RunOutcome, RunRepository, RunStatus
from highland.runtime.approvals import ApprovalStore
from highland.runtime.cancellation import RunCancellationStore
from highland.runtime.events import RunEventStore
from highland.runtime.mcp import MCPGateway
from highland.runtime.policy import RunScope, ToolRegistry

from .conversations import ConversationMessage, ConversationStore, SourceReference

_SOURCE_SEARCH_RESULT_LIMIT = 8
_SOURCE_SEARCH_PASSAGE_LIMIT = 30


def _completed_prior_messages(
    messages: list[ConversationMessage],
    *,
    current_run_id: str,
) -> list[Message]:
    """Keep only complete, non-empty exchanges from earlier runs."""
    roles_by_run: dict[str, set[str]] = {}
    for message in messages:
        if message.run_id and message.run_id != current_run_id and message.content.strip():
            roles_by_run.setdefault(message.run_id, set()).add(message.role)
    completed_runs = {
        run_id
        for run_id, roles in roles_by_run.items()
        if {MessageRole.USER.value, MessageRole.ASSISTANT.value} <= roles
    }
    return [
        Message(role=MessageRole(message.role), content=message.content)
        for message in messages
        if message.run_id in completed_runs
        and message.role in {MessageRole.USER.value, MessageRole.ASSISTANT.value}
        and message.content.strip()
    ]


class DiscoverModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class DiscoverFilters(DiscoverModel):
    customer_id: str | None = None
    source_types: set[str] = Field(default_factory=set)
    allowed_visibilities: set[str] = Field(default_factory=set)
    updated_after: datetime | None = None
    updated_before: datetime | None = None

    def retrieval(self) -> RetrievalFilters:
        return RetrievalFilters.model_validate(self.model_dump())


class SearchRequest(DiscoverModel):
    query: str = Field(min_length=1, max_length=20_000)
    filters: DiscoverFilters = Field(default_factory=DiscoverFilters)


class ChatRequest(SearchRequest):
    conversation_id: str


class SearchSourceResult(DiscoverModel):
    source_system: str
    source_id: str
    title: str
    source_type: str
    source_url: str
    customer_id: str | None = None
    updated_at: datetime
    metadata: dict[str, str | int | float | bool | None] = Field(default_factory=dict)
    score: float
    passages: list[RetrievalResult]


class SearchResponse(DiscoverModel):
    query: str
    results: list[SearchSourceResult]
    diagnostics: list[CandidateDiagnostics]
    timings: RetrievalTimings
    rerank_usage: Usage = Field(default_factory=Usage)
    rerank_model: str | None = None


def aggregate_search_results(
    retrieval: RetrievalResponse,
    *,
    source_limit: int = _SOURCE_SEARCH_RESULT_LIMIT,
) -> SearchResponse:
    """Collapse ranked passages into source records without losing passage provenance."""
    if source_limit <= 0:
        raise ValueError("source_limit must be positive")

    grouped: dict[tuple[str, str], list[RetrievalResult]] = {}
    ordered_keys: list[tuple[str, str]] = []
    for passage in retrieval.results:
        key = (passage.chunk.source_system, passage.chunk.source_id)
        if key not in grouped:
            if len(ordered_keys) >= source_limit:
                continue
            grouped[key] = []
            ordered_keys.append(key)
        grouped[key].append(passage)

    results: list[SearchSourceResult] = []
    for key in ordered_keys:
        ranked_passages = grouped[key]
        representative = ranked_passages[0].chunk
        passages = sorted(
            ranked_passages,
            key=lambda item: (item.chunk.location.ordinal, -item.score, item.chunk.id),
        )
        results.append(
            SearchSourceResult(
                source_system=representative.source_system,
                source_id=representative.source_id,
                title=representative.title,
                source_type=representative.source_type,
                source_url=representative.source_url,
                customer_id=representative.customer_id,
                updated_at=representative.updated_at,
                metadata=representative.metadata,
                score=max(item.score for item in ranked_passages),
                passages=passages,
            )
        )

    return SearchResponse(
        query=retrieval.query,
        results=results,
        diagnostics=retrieval.diagnostics,
        timings=retrieval.timings,
        rerank_usage=retrieval.rerank_usage,
        rerank_model=retrieval.rerank_model,
    )


class DiscoverService:
    def __init__(
        self,
        *,
        index_dir: Path,
        conversations: ConversationStore,
        runs_dir: Path,
        provider: ModelProvider,
        profile: AgentProfile,
        tool_policy: Path,
        connector_commands: dict[str, tuple[str, ...]],
        connector_timeout_seconds: float,
        trace_context: dict[str, object] | None = None,
    ) -> None:
        self.index_dir = index_dir
        self.conversations = conversations
        self.runs_dir = runs_dir
        self.provider = provider
        self.profile = profile
        self.tool_policy = tool_policy
        self.connector_commands = connector_commands
        self.connector_timeout_seconds = connector_timeout_seconds
        self.trace_context = trace_context or {}

    def _retriever(self, *, result_limit: int = 8) -> HybridRetriever:
        chunks = load_chunks(self.index_dir)
        if not chunks:
            raise FileNotFoundError("Search index is missing; run index sync first")
        indexer = EmbeddingIndex(self.provider.embeddings)
        vectors = FaissStore.open(
            self.index_dir / "vectors",
            expected_model_id=indexer.model_id,
        )
        return HybridRetriever(
            chunks,
            vector_store=vectors,
            embedding_index=indexer,
            reranker=self.provider.rerank,
            result_limit=result_limit,
        )

    async def search(self, request: SearchRequest) -> RetrievalResponse:
        return await self._retriever().search(
            request.query,
            filters=request.filters.retrieval(),
        )

    async def search_sources(self, request: SearchRequest) -> SearchResponse:
        retrieval = await self._retriever(result_limit=_SOURCE_SEARCH_PASSAGE_LIMIT).search(
            request.query,
            filters=request.filters.retrieval(),
        )
        return aggregate_search_results(retrieval)

    async def chat(self, request: ChatRequest, *, run_id: str) -> RunOutcome:
        retrieval = await self.search(request)
        prior = self.conversations.context(
            request.conversation_id,
            max_chars=self.profile.budgets.max_context_chars,
        )
        prior_messages = _completed_prior_messages(prior, current_run_id=run_id)
        documents = [
            Document(
                id=result.chunk.id,
                text=result.chunk.text,
                metadata={
                    "source_system": result.chunk.source_system,
                    "source_id": result.chunk.source_id,
                    "source_url": result.chunk.source_url,
                    "source_type": result.chunk.source_type,
                    "customer_id": result.chunk.customer_id,
                    "section": result.chunk.location.section,
                    "updated_at": result.chunk.updated_at.isoformat(),
                },
            )
            for result in retrieval.results
        ]
        event_store = RunEventStore(self.runs_dir / "events")
        event_store.append(
            run_id,
            "retrieval",
            {
                "query": request.query,
                "filters": request.filters.model_dump(mode="json"),
                "results": [
                    {
                        "chunk_id": result.chunk.id,
                        "source_id": result.chunk.source_id,
                        "score": result.score,
                        "chunk": result.chunk.model_dump(mode="json"),
                    }
                    for result in retrieval.results
                ],
                "diagnostics": [item.model_dump(mode="json") for item in retrieval.diagnostics],
                "timings": retrieval.timings.model_dump(mode="json"),
                "rerank_usage": retrieval.rerank_usage.model_dump(mode="json"),
                "rerank_model": retrieval.rerank_model,
                "execution": self.trace_context,
            },
        )
        gateway = MCPGateway(
            self.connector_commands,
            startup_timeout_seconds=self.connector_timeout_seconds,
            request_timeout_seconds=self.connector_timeout_seconds,
        )
        await gateway.start()
        try:
            loop = AgentLoop(
                self.provider.chat,
                ToolRegistry.from_file(gateway, self.tool_policy),
                self.profile,
                RunRepository(self.runs_dir / "state"),
                ApprovalStore(self.runs_dir / "approvals"),
                event_store,
                RunCancellationStore(self.runs_dir / "cancellations"),
            )
            outcome = await loop.run(
                run_id=run_id,
                user_message=request.query,
                scope=RunScope(
                    allowed_customers=(
                        frozenset({request.filters.customer_id})
                        if request.filters.customer_id
                        else frozenset()
                    ),
                    allowed_visibilities=frozenset(request.filters.allowed_visibilities),
                ),
                documents=documents,
                prior_messages=prior_messages,
            )
        finally:
            await gateway.close()
        sources_by_chunk = {
            result.chunk.id: SourceReference(
                chunk_id=result.chunk.id,
                source_id=result.chunk.source_id,
                source_url=result.chunk.source_url,
                source_system=result.chunk.source_system,
                source_type=result.chunk.source_type,
                title=result.chunk.title,
                passage=result.chunk.text,
                section=result.chunk.location.section,
                updated_at=result.chunk.updated_at,
                customer_id=result.chunk.customer_id,
                score=result.score,
            )
            for result in retrieval.results
        }
        cited = {source_id for citation in outcome.citations for source_id in citation.source_ids}
        sources = [
            sources_by_chunk[source_id] for source_id in cited if source_id in sources_by_chunk
        ]
        if outcome.status is RunStatus.COMPLETED and outcome.content.strip():
            self.conversations.append_message(
                request.conversation_id,
                role="assistant",
                content=outcome.content,
                run_id=run_id,
                sources=sources,
            )
        return outcome


__all__ = [
    "ChatRequest",
    "DiscoverFilters",
    "DiscoverService",
    "SearchRequest",
    "SearchResponse",
    "SearchSourceResult",
    "VectorIndexError",
    "aggregate_search_results",
]
