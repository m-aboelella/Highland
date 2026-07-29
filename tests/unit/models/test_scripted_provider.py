from __future__ import annotations

from pathlib import Path

import pytest

from highland.models.contracts import (
    ChatRequest,
    ChatResponse,
    Document,
    EmbeddingRequest,
    FinishReason,
    InputType,
    Message,
    MessageRole,
    ModelError,
    RerankRequest,
    ResponseMetadata,
    ToolCall,
)
from highland.models.scripted import (
    DeterministicEmbeddingModel,
    MalformedScriptedResponse,
    ScriptedChatModel,
    ScriptedChatStep,
    ScriptedFailure,
    ScriptedRerankModel,
    ScriptedRerankStep,
    UnexpectedModelCall,
    load_named_chat_script,
)


def response(content: str = "done") -> ChatResponse:
    return ChatResponse(
        message=Message(
            role=MessageRole.ASSISTANT,
            content=content,
            tool_calls=[ToolCall(id="call_1", name="tickets.get", arguments={"id": "4182"})],
        ),
        finish_reason=FinishReason.TOOL_CALL,
        metadata=ResponseMetadata(provider="fixture", model="fixture"),
    )


@pytest.mark.asyncio
async def test_chat_is_queued_recorded_and_always_labelled_simulated() -> None:
    provider = ScriptedChatModel([response()])
    request = ChatRequest(messages=[Message(role=MessageRole.USER, content="Investigate")])

    result = await provider.chat(request)

    assert provider.requests == [request]
    assert result.metadata.simulated
    assert result.metadata.provider == "scripted"
    assert result.message.tool_calls[0].name == "tickets.get"
    with pytest.raises(UnexpectedModelCall, match="queue is exhausted"):
        await provider.chat(request)


@pytest.mark.asyncio
async def test_streaming_uses_declared_fragments_and_final_response() -> None:
    provider = ScriptedChatModel(
        [ScriptedChatStep(response=response("hello world"), stream_fragments=("hello ", "world"))]
    )

    events = [
        event
        async for event in provider.stream(
            ChatRequest(messages=[Message(role=MessageRole.USER, content="Stream")])
        )
    ]

    assert [event.text for event in events if event.type == "text-delta"] == ["hello ", "world"]
    assert events[-1].response is not None
    assert events[-1].response.metadata.simulated


@pytest.mark.asyncio
async def test_scripted_failures_and_malformed_responses_are_explicit() -> None:
    request = ChatRequest(messages=[Message(role=MessageRole.USER, content="Fail")])
    limited = ScriptedChatModel([ScriptedFailure.rate_limit()])
    malformed = ScriptedChatModel([{"not": "a chat response"}])

    with pytest.raises(ModelError) as failure:
        await limited.chat(request)
    assert failure.value.code == "rate_limit"
    assert failure.value.retryable
    with pytest.raises(MalformedScriptedResponse):
        await malformed.chat(request)


@pytest.mark.asyncio
async def test_embeddings_are_stable_and_requests_are_recorded() -> None:
    provider = DeterministicEmbeddingModel(dimensions=8)
    request = EmbeddingRequest(texts=["same", "different", "same"], input_type=InputType.SEARCH_DOCUMENT)

    first = await provider.embed(request)
    second = await provider.embed(request)

    assert first.vectors == second.vectors
    assert first.vectors[0] == first.vectors[2]
    assert first.vectors[0] != first.vectors[1]
    assert len(first.vectors[0]) == 8
    assert len(provider.requests) == 2
    assert first.metadata.simulated


@pytest.mark.asyncio
async def test_scripted_reranking_orders_and_limits_results() -> None:
    documents = [
        Document(id="a", text="first"),
        Document(id="b", text="second"),
        Document(id="c", text="third"),
    ]
    provider = ScriptedRerankModel([ScriptedRerankStep(scores=(0.2, 0.9, 0.4))])

    result = await provider.rerank(RerankRequest(query="query", documents=documents, top_n=2))

    assert [item.document.id for item in result.results] == ["b", "c"]
    assert result.metadata.simulated
    assert provider.requests[0].query == "query"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "name",
    [
        "customer-meeting-preparation",
        "deployment-issue-investigation",
        "weekly-customer-health",
    ],
)
async def test_named_showcase_scripts_are_explicitly_simulated(name: str) -> None:
    root = Path(__file__).resolve().parents[3]
    provider = load_named_chat_script(name, scripts_dir=root / "config" / "model_scripts")

    result = await provider.chat(
        ChatRequest(messages=[Message(role=MessageRole.USER, content="Run showcase")])
    )

    assert result.metadata.simulated
    assert "Simulated" in result.message.content
