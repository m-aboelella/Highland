from __future__ import annotations

from datetime import datetime
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field

from highland.models.contracts import Document, Message, MessageRole
from highland.models.provider import ModelProvider
from highland.retrieval.faiss_store import EmbeddingIndex, FaissStore, VectorIndexError
from highland.retrieval.hybrid import HybridRetriever, RetrievalFilters, RetrievalResponse
from highland.retrieval.sync import load_chunks
from highland.runtime.agent import AgentLoop, AgentProfile, RunOutcome, RunRepository
from highland.runtime.approvals import ApprovalStore
from highland.runtime.cancellation import RunCancellationStore
from highland.runtime.events import RunEventStore
from highland.runtime.mcp import MCPGateway
from highland.runtime.policy import RunScope, ToolRegistry

from .conversations import ConversationStore, SourceReference


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

    def _retriever(self) -> HybridRetriever:
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
        )

    async def search(self, request: SearchRequest) -> RetrievalResponse:
        return await self._retriever().search(
            request.query,
            filters=request.filters.retrieval(),
        )

    async def chat(self, request: ChatRequest, *, run_id: str) -> RunOutcome:
        retrieval = await self.search(request)
        prior = self.conversations.context(
            request.conversation_id,
            max_chars=self.profile.budgets.max_context_chars,
        )
        prior_messages = [
            Message(role=MessageRole(message.role), content=message.content)
            for message in prior
            if message.role in {MessageRole.USER.value, MessageRole.ASSISTANT.value}
            and message.run_id != run_id
        ]
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
    "VectorIndexError",
]
