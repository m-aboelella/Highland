from __future__ import annotations

import json
from pathlib import Path
from typing import ClassVar

import pytest

from highland.models.contracts import (
    ChatResponse,
    Citation,
    Document,
    FinishReason,
    Message,
    MessageRole,
    ToolCall,
)
from highland.models.scripted import (
    DeterministicEmbeddingModel,
    DeterministicRerankModel,
    ScriptedChatModel,
    simulated_metadata,
)
from highland.retrieval.contracts import Chunk, chunk_document
from highland.retrieval.faiss_store import EmbeddingIndex
from highland.retrieval.hybrid import HybridRetriever, RetrievalFilters
from highland.retrieval.ingestion import normalize_record
from highland.runtime.agent import AgentLoop, AgentProfile, RunRepository, RunStatus
from highland.runtime.mcp import MCPTool, NormalizedToolResult
from highland.runtime.policy import RunScope, ToolRegistry

ROOT = Path(__file__).parents[2]
CUSTOMER = "cus_northwind"


def _records() -> dict[str, list[dict[str, object]]]:
    mapping = {
        "crm": ("crm.json", ("customers",)),
        "knowledge": ("knowledge.json", ("documents",)),
        "support": ("support.json", ("tickets",)),
        "observability": ("observability.json", ("deployments", "incidents")),
        "communications": ("communications.json", ("messages", "meetings")),
        "projects": ("projects.json", ("issues",)),
    }
    records: dict[str, list[dict[str, object]]] = {}
    for source, (filename, collections) in mapping.items():
        payload = json.loads((ROOT / "data" / "seed" / filename).read_text("utf-8"))
        records[source] = [item for collection in collections for item in payload[collection]]
    return records


class SeedGateway:
    READS: ClassVar[dict[str, str]] = {
        "crm__get_customer": "crm",
        "communications__list_customer_meetings": "communications",
        "support__list_customer_tickets": "support",
        "observability__get_deployment": "observability",
        "observability__list_incidents": "observability",
        "projects__list_project_issues": "projects",
        "knowledge__search_documents": "knowledge",
        "communications__search_messages": "communications",
    }
    WRITES: ClassVar[dict[str, str]] = {
        "support__create_ticket": "support",
        "projects__create_project_issue": "projects",
    }

    def __init__(self, records: dict[str, list[dict[str, object]]]) -> None:
        self.records = records
        self.calls: list[str] = []
        self.tools = [
            MCPTool(
                connector=name.partition("__")[0],
                source_name=name.partition("__")[2],
                qualified_name=name,
                description=f"Synthetic {name}",
                input_schema={
                    "type": "object",
                    "properties": {
                        "customer_id": {"type": "string"},
                        "query": {"type": "string"},
                    },
                    "required": ["customer_id"],
                    "additionalProperties": True,
                },
            )
            for name in sorted(self.READS | self.WRITES)
        ]

    def model_tools(self):
        return [tool.model_definition() for tool in self.tools]

    async def call(self, qualified_name, arguments):
        self.calls.append(qualified_name)
        source = self.READS[qualified_name]
        items = [
            item
            for item in self.records[source]
            if item.get("customer_id") in (None, arguments.get("customer_id"))
            and (
                qualified_name != "crm__get_customer"
                or item.get("id") == arguments.get("customer_id")
            )
        ]
        return NormalizedToolResult(
            connector=qualified_name.partition("__")[0],
            tool=qualified_name,
            content=json.dumps({"items": items}, sort_keys=True),
        )


def _chunks(records: dict[str, list[dict[str, object]]]) -> list[Chunk]:
    indexed = {
        "archive": records["knowledge"],
        "relay": records["support"],
        "beacon": [
            item for item in records["observability"] if "severity" in item
        ],
        "pulse": records["communications"],
        "track": records["projects"],
    }
    return [
        chunk
        for source, items in indexed.items()
        for item in items
        for document in normalize_record(source, item)
        for chunk in chunk_document(document)
    ]


def _call(call_id: str, name: str, **arguments: str) -> ToolCall:
    return ToolCall(id=call_id, name=name, arguments=arguments)


@pytest.mark.asyncio
async def test_customer_meeting_preparation_is_grounded_and_read_only(tmp_path: Path) -> None:
    records = _records()
    chunks = _chunks(records)
    embeddings = DeterministicEmbeddingModel(dimensions=16)
    indexer = EmbeddingIndex(embeddings)
    vectors = await indexer.build(chunks, tmp_path / "vectors")
    retrieval = await HybridRetriever(
        chunks,
        vector_store=vectors,
        embedding_index=indexer,
        rerankers={"fast": DeterministicRerankModel()},
        candidate_limit=len(chunks),
        result_limit=len(chunks),
    ).search(
        "Northwind meeting deployment latency compaction capacity review SLO renewal",
        filters=RetrievalFilters(customer_id=CUSTOMER),
    )
    retrieved = [result.chunk for result in retrieval.results]
    by_source_id = {chunk.source_id: chunk for chunk in retrieved}
    expected_indexed = {
        "tkt_1042",
        "inc_208",
        "msg_7002",
        "iss_91",
        "iss_92",
        "doc_northwind_success_plan",
    }
    assert expected_indexed <= by_source_id.keys()

    calls = (
        _call("crm", "crm__get_customer", customer_id=CUSTOMER),
        _call("meetings", "communications__list_customer_meetings", customer_id=CUSTOMER),
        _call("tickets", "support__list_customer_tickets", customer_id=CUSTOMER),
        _call("deployment", "observability__get_deployment", customer_id=CUSTOMER),
        _call("incidents", "observability__list_incidents", customer_id=CUSTOMER),
        _call("projects", "projects__list_project_issues", customer_id=CUSTOMER),
        _call(
            "runbook",
            "knowledge__search_documents",
            customer_id=CUSTOMER,
            query="latency compaction capacity review",
        ),
        _call(
            "messages",
            "communications__search_messages",
            customer_id=CUSTOMER,
            query="latency compaction hypothesis",
        ),
    )
    content = (
        "## Deployment\nNorthwind is a strategic private-cloud customer running 4.18.2 "
        "with an October 31 renewal.\n\n"
        "## Recent issues\nThe active P1 is elevated production retrieval latency. "
        "Compaction memory contention is a hypothesis, not a confirmed root cause.\n\n"
        "## Unresolved actions\nThe capacity review and latency SLO remain open.\n\n"
        "## Talking points\nReview controlled-run evidence, Q4 ingestion forecasts, "
        "and renewal success criteria."
    )
    citations = [
        Citation(
            start=14,
            end=92,
            text="deployment",
            tool_call_ids=["crm", "deployment", "meetings"],
        ),
        Citation(
            start=112,
            end=245,
            text="issues",
            source_ids=[
                by_source_id["tkt_1042"].id,
                by_source_id["inc_208"].id,
                by_source_id["msg_7002"].id,
            ],
            tool_call_ids=["tickets", "incidents", "messages"],
        ),
        Citation(
            start=270,
            end=325,
            text="actions",
            source_ids=[
                by_source_id["iss_91"].id,
                by_source_id["iss_92"].id,
                by_source_id["doc_northwind_success_plan"].id,
            ],
            tool_call_ids=["projects", "runbook"],
        ),
    ]
    model = ScriptedChatModel(
        [
            ChatResponse(
                message=Message(role=MessageRole.ASSISTANT, tool_calls=list(calls)),
                finish_reason=FinishReason.TOOL_CALL,
                metadata=simulated_metadata("meeting-acceptance"),
            ),
            ChatResponse(
                message=Message(role=MessageRole.ASSISTANT, content=content),
                citations=citations,
                finish_reason=FinishReason.COMPLETE,
                metadata=simulated_metadata("meeting-acceptance"),
            ),
        ]
    )
    gateway = SeedGateway(records)
    runtime = AgentLoop(
        model,
        ToolRegistry.from_file(gateway, ROOT / "config" / "tool_policy.json"),
        AgentProfile.load(ROOT / "config" / "agents" / "general.json"),
        RunRepository(tmp_path / "runs"),
    )
    outcome = await runtime.run(
        run_id="meeting",
        user_message="Prepare the Northwind meeting.",
        scope=RunScope(
            allowed_customers=frozenset({CUSTOMER}),
            allow_writes=False,
        ),
        documents=[
            Document(
                id=result.chunk.id,
                text=result.chunk.text,
                metadata={
                    "source_id": result.chunk.source_id,
                    "source_system": result.chunk.source_system,
                    "customer_id": result.chunk.customer_id,
                    "source_url": result.chunk.source_url,
                },
            )
            for result in retrieval.results
        ],
    )

    assert outcome.status is RunStatus.COMPLETED
    for heading in ("## Deployment", "## Recent issues", "## Unresolved actions", "## Talking points"):
        assert heading in outcome.content
    lowered = outcome.content.lower()
    assert "hypothesis" in lowered and "not a confirmed root cause" in lowered
    assert set(gateway.calls) == set(SeedGateway.READS)
    assert not set(gateway.calls) & set(SeedGateway.WRITES)
    offered = {tool.name for request in model.requests for tool in request.tools}
    assert not offered & set(SeedGateway.WRITES)
    assert all(call.arguments.get("customer_id") == CUSTOMER for call in calls)
    cited_chunks = {source_id for citation in outcome.citations for source_id in citation.source_ids}
    assert {
        by_source_id[source_id].id
        for source_id in expected_indexed
    } <= cited_chunks
    serialized = json.dumps(
        [
            result.chunk.model_dump(mode="json")
            for result in retrieval.results
        ]
    )
    assert "cus_alpine" not in serialized
    assert "cus_lumon" not in serialized
