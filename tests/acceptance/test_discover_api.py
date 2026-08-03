from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from pathlib import Path

from fastapi.testclient import TestClient

from highland.api import create_app
from highland.models.contracts import (
    ChatResponse,
    Citation,
    FinishReason,
    Message,
    MessageRole,
)
from highland.models.provider import ModelProvider
from highland.models.scripted import (
    DeterministicEmbeddingModel,
    DeterministicRerankModel,
    ScriptedChatModel,
    simulated_metadata,
)
from highland.retrieval.contracts import SourceDocument, chunk_document
from highland.retrieval.faiss_store import EmbeddingIndex
from highland.retrieval.ingestion import promote_snapshot
from highland.settings import HighlandSettings


def document(source_id: str, text: str, customer: str) -> SourceDocument:
    return SourceDocument(
        source_system="archive",
        source_id=source_id,
        title=source_id,
        text=text,
        source_type="runbook",
        visibility="internal",
        updated_at=datetime(2026, 7, 1, tzinfo=UTC),
        source_url=f"mock://archive/{source_id}",
        customer_id=customer,
    )


def client_with_index(tmp_path: Path) -> tuple[TestClient, ScriptedChatModel, str]:
    northwind = document(
        "doc_northwind",
        (
            "# Symptoms and hypothesis\n"
            "Northwind compaction contention caused retrieval latency.\n\n"
            "# Mitigation\n"
            "Compaction is paused while the team validates memory pressure.\n\n"
            "# Evidence\n"
            "Shard memory exceeded the expected operating range."
        ),
        "cus_northwind",
    )
    alpine = document("doc_alpine", "Alpine unrelated confidential roadmap.", "cus_alpine")
    chunks = [chunk for item in (northwind, alpine) for chunk in chunk_document(item)]
    embeddings = DeterministicEmbeddingModel()
    index_dir = tmp_path / "indexes" / "search"
    now = datetime.now(UTC)
    promote_snapshot(
        index_dir,
        chunks=chunks,
        documents=[northwind, alpine],
        started=now,
        completed=now,
        counts={"archive": 2},
    )
    asyncio.run(EmbeddingIndex(embeddings).build(chunks, index_dir / "vectors"))
    cited_id = chunks[0].id
    chat = ScriptedChatModel(
        [
            ChatResponse(
                message=Message(
                    role=MessageRole.ASSISTANT,
                    content="Compaction contention increased latency.",
                ),
                citations=[
                    Citation(
                        start=0,
                        end=21,
                        text="Compaction contention",
                        source_ids=[cited_id],
                    )
                ],
                finish_reason=FinishReason.COMPLETE,
                metadata=simulated_metadata("scripted-chat"),
            ),
            ChatResponse(
                message=Message(role=MessageRole.ASSISTANT, content="It remains a hypothesis."),
                finish_reason=FinishReason.COMPLETE,
                metadata=simulated_metadata("scripted-chat"),
            ),
        ]
    )
    provider = ModelProvider(
        chat=chat,
        embeddings=embeddings,
        rerank=DeterministicRerankModel(),
    )
    settings = HighlandSettings(workspace_dir=tmp_path, connector_commands={})
    return TestClient(create_app(settings, model_provider=provider)), chat, cited_id


def test_search_aggregates_filtered_passages_without_chat(tmp_path: Path) -> None:
    client, chat, _ = client_with_index(tmp_path)
    response = client.post(
        "/discover/search",
        json={
            "query": "compaction latency",
            "filters": {
                "customer_id": "cus_northwind",
                "allowed_visibilities": ["internal"],
            },
        },
    )
    assert response.status_code == 200
    assert not chat.requests
    results = response.json()["results"]
    assert len(results) == 1
    assert results[0]["source_id"] == "doc_northwind"
    assert results[0]["customer_id"] == "cus_northwind"
    assert results[0]["score"] == max(item["score"] for item in results[0]["passages"])
    assert [item["chunk"]["location"]["section"] for item in results[0]["passages"]] == [
        "Symptoms and hypothesis",
        "Mitigation",
        "Evidence",
    ]
    assert len({item["chunk"]["id"] for item in results[0]["passages"]}) == 3


def test_application_exposes_effective_models_limits_and_route_groups(tmp_path: Path) -> None:
    client, _, _ = client_with_index(tmp_path)

    workspace = client.get("/workspace/status")
    agents = client.get("/agents")

    assert workspace.status_code == 200
    assert workspace.json()["models"] == {
        "chat": "scripted-chat",
        "embedding": "deterministic-embedding",
        "rerank": "deterministic-rerank",
    }
    assert set(workspace.json()["limits"]) == {"agent", "provider"}
    assert "model" not in agents.json()[0]
    assert agents.json()[0]["models"] == workspace.json()["models"]
    tags = {
        tag
        for operations in client.get("/openapi.json").json()["paths"].values()
        for operation in operations.values()
        for tag in operation.get("tags", [])
    }
    assert tags == {
        "discover and runs",
        "artifacts",
        "workflows",
        "platform operations",
    }


def test_chat_preserves_citations_context_and_current_filters(tmp_path: Path) -> None:
    client, chat, cited_id = client_with_index(tmp_path)
    conversation_id = client.post("/conversations", json={"title": "Review"}).json()["id"]
    request = {
        "conversation_id": conversation_id,
        "query": "What caused the latency?",
        "filters": {
            "customer_id": "cus_northwind",
            "allowed_visibilities": ["internal"],
        },
    }
    first = client.post("/discover/chat", json=request)
    assert first.status_code == 200
    assert first.json()["citations"][0]["source_ids"] == [cited_id]
    assert first.json()["sources"][0]["chunk_id"] == cited_id
    assert len(chat.requests[0].documents) == 3

    second = client.post(
        "/discover/chat",
        json={**request, "query": "How certain is that?"},
    )
    assert second.status_code == 200
    assert any(
        message.content == "Compaction contention increased latency."
        for message in chat.requests[1].messages
    )
    assert all(
        document.metadata["customer_id"] == "cus_northwind"
        for document in chat.requests[1].documents
    )
