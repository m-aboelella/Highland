from __future__ import annotations

from types import SimpleNamespace

import cohere
import httpx
import pytest
from cohere.types import (
    ApiMeta,
    ApiMetaBilledUnits,
    ApiMetaTokens,
    AssistantMessageResponse,
    DocumentSource,
    EmbedByTypeResponse,
    EmbedByTypeResponseEmbeddings,
    TextAssistantMessageResponseContentItem,
    ToolCallV2,
    ToolCallV2Function,
    ToolSource,
    UsageBilledUnits,
    UsageTokens,
)
from cohere.types import Citation as CohereCitation
from cohere.types import Usage as CohereUsage
from cohere.v2.types import V2ChatResponse, V2RerankResponse, V2RerankResponseResultsItem

from highland.models.cohere import CohereChatModel, CohereEmbeddingModel, CohereRerankModel
from highland.models.contracts import (
    ChatRequest,
    Document,
    EmbeddingRequest,
    FinishReason,
    InputType,
    Message,
    MessageRole,
    ModelError,
    RerankRequest,
    ToolDefinition,
    ToolResult,
)
from highland.models.provider import build_model_provider
from highland.models.scripted import ScriptedChatModel
from highland.settings import HighlandSettings, ModelBackend


def chat_response(content: str = "Ticket 4182 is open.") -> V2ChatResponse:
    return V2ChatResponse(
        id="req_chat",
        finish_reason="TOOL_CALL",
        message=AssistantMessageResponse(
            content=[TextAssistantMessageResponseContentItem(text=content)],
            tool_calls=[
                ToolCallV2(
                    id="call_1",
                    function=ToolCallV2Function(
                        name="support.get_ticket",
                        arguments='{"ticket_id":"tkt_4182"}',
                    ),
                )
            ],
            citations=[
                CohereCitation(
                    start=0,
                    end=11,
                    text="Ticket 4182",
                    sources=[
                        DocumentSource(id="doc_1", document={"text": "evidence"}),
                        ToolSource(id="call_1:0", tool_output={"status": "open"}),
                    ],
                )
            ],
        ),
        usage=CohereUsage(
            tokens=UsageTokens(input_tokens=20, output_tokens=8),
            billed_units=UsageBilledUnits(input_tokens=19, output_tokens=8),
        ),
    )


class FakeClient:
    def __init__(self) -> None:
        self.chat_calls: list[dict[str, object]] = []
        self.embed_calls: list[dict[str, object]] = []
        self.rerank_calls: list[dict[str, object]] = []
        self.chat_failures = 0

    async def chat(self, **kwargs):
        self.chat_calls.append(kwargs)
        if self.chat_failures:
            self.chat_failures -= 1
            raise httpx.ConnectError("temporary")
        return chat_response()

    async def embed(self, **kwargs):
        self.embed_calls.append(kwargs)
        return EmbedByTypeResponse(
            id="req_embed",
            embeddings=EmbedByTypeResponseEmbeddings(float=[[0.1, 0.2], [0.3, 0.4]]),
            meta=ApiMeta(
                tokens=ApiMetaTokens(input_tokens=4),
                billed_units=ApiMetaBilledUnits(input_tokens=4),
            ),
        )

    async def rerank(self, **kwargs):
        self.rerank_calls.append(kwargs)
        return V2RerankResponse(
            id="req_rerank",
            results=[
                V2RerankResponseResultsItem(index=1, relevance_score=0.9),
                V2RerankResponseResultsItem(index=0, relevance_score=0.2),
            ],
            meta=ApiMeta(billed_units=ApiMetaBilledUnits(search_units=1)),
        )

    async def chat_stream(self, **_kwargs):
        yield SimpleNamespace(type="message-start")
        yield SimpleNamespace(
            type="content-delta",
            delta=SimpleNamespace(
                message=SimpleNamespace(content=SimpleNamespace(text="hello"))
            ),
        )
        yield SimpleNamespace(
            type="message-end",
            id="req_stream",
            delta=SimpleNamespace(
                finish_reason="COMPLETE",
                usage=CohereUsage(tokens=UsageTokens(input_tokens=2, output_tokens=1)),
            ),
        )


def request() -> ChatRequest:
    return ChatRequest(
        messages=[
            Message(role=MessageRole.SYSTEM, content="Be grounded."),
            Message(role=MessageRole.USER, content="Get the ticket."),
            Message(
                role=MessageRole.TOOL,
                tool_results=[
                    ToolResult(tool_call_id="prior_call", content='{"status":"open"}')
                ],
            ),
        ],
        tools=[
            ToolDefinition(
                name="support.get_ticket",
                description="Fetch a support ticket",
                input_schema={
                    "type": "object",
                    "properties": {"ticket_id": {"type": "string"}},
                    "required": ["ticket_id"],
                },
            )
        ],
        documents=[Document(id="doc_1", text="ticket evidence", metadata={"visibility": "internal"})],
        logical_call_id="logical_1",
    )


@pytest.mark.asyncio
async def test_chat_maps_requests_responses_tools_citations_usage_and_ids() -> None:
    client = FakeClient()
    provider = CohereChatModel(client, model="command-a-plus-05-2026")

    result = await provider.chat(request())

    arguments = client.chat_calls[0]
    assert arguments["model"] == "command-a-plus-05-2026"
    assert arguments["tools"][0]["function"]["parameters"]["required"] == ["ticket_id"]
    assert arguments["documents"][0]["data"]["visibility"] == "internal"
    assert arguments["messages"][2]["tool_call_id"] == "prior_call"
    assert result.finish_reason is FinishReason.TOOL_CALL
    assert result.message.tool_calls[0].arguments == {"ticket_id": "tkt_4182"}
    assert result.citations[0].source_ids == ["doc_1"]
    assert result.citations[0].tool_call_ids == ["call_1"]
    assert result.usage.input_tokens == 20
    assert result.metadata.request_id == "req_chat"
    assert result.metadata.logical_call_id == "logical_1"
    assert result.metadata.raw_response is None


@pytest.mark.asyncio
async def test_transient_chat_retry_keeps_one_logical_call_id() -> None:
    client = FakeClient()
    client.chat_failures = 1
    provider = CohereChatModel(client, model="command-a-plus-05-2026", max_retries=1)

    result = await provider.chat(request())

    assert len(client.chat_calls) == 2
    assert result.metadata.logical_call_id == "logical_1"


@pytest.mark.asyncio
async def test_invalid_request_preserves_safe_provider_detail_and_request_id() -> None:
    class RejectingClient:
        async def chat(self, **_kwargs):
            raise cohere.BadRequestError(
                body={"message": "message must not be empty in a turn"},
                headers={"x-request-id": "req_invalid"},
            )

    provider = CohereChatModel(RejectingClient(), model="command-a-plus-05-2026")

    with pytest.raises(ModelError, match="message must not be empty in a turn") as failure:
        await provider.chat(request())

    assert failure.value.code == "invalid_request"
    assert failure.value.request_id == "req_invalid"


@pytest.mark.asyncio
async def test_stream_maps_text_and_final_usage() -> None:
    provider = CohereChatModel(FakeClient(), model="command-a-plus-05-2026")

    events = [event async for event in provider.stream(request())]

    assert [event.type for event in events] == ["message-start", "text-delta", "message-end"]
    assert events[1].text == "hello"
    assert events[-1].response is not None
    assert events[-1].response.message.content == "hello"
    assert events[-1].response.usage.total_tokens == 3


@pytest.mark.asyncio
async def test_embed_and_rerank_mapping() -> None:
    client = FakeClient()
    embedder = CohereEmbeddingModel(client, model="embed-v4.0")
    reranker = CohereRerankModel(client, model="rerank-v4.0-fast")
    documents = [Document(id="a", text="first"), Document(id="b", text="second")]

    embedded = await embedder.embed(
        EmbeddingRequest(texts=["first", "second"], input_type=InputType.SEARCH_DOCUMENT)
    )
    reranked = await reranker.rerank(
        RerankRequest(query="second", documents=documents, top_n=2)
    )

    assert embedded.vectors == [[0.1, 0.2], [0.3, 0.4]]
    assert client.embed_calls[0]["embedding_types"] == ["float"]
    assert embedded.metadata.request_id == "req_embed"
    assert [result.document.id for result in reranked.results] == ["b", "a"]
    assert reranked.usage.search_units == 1


def test_scripted_backend_never_constructs_a_cohere_client(monkeypatch) -> None:
    def unexpected_client(*_args, **_kwargs):
        raise AssertionError("Cohere client constructed in scripted mode")

    monkeypatch.setattr("highland.models.provider.cohere.AsyncClientV2", unexpected_client)

    provider = build_model_provider(HighlandSettings(model_backend=ModelBackend.SCRIPTED))

    assert isinstance(provider.chat, ScriptedChatModel)
