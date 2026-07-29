from __future__ import annotations

import asyncio
import json
import time
from collections.abc import AsyncIterator, Awaitable, Callable
from typing import Any, TypeVar

import cohere
import httpx

from .contracts import (
    ChatRequest,
    ChatResponse,
    ChatStreamEvent,
    Citation,
    EmbeddingRequest,
    EmbeddingResponse,
    FinishReason,
    Message,
    MessageRole,
    ModelCapabilities,
    ModelError,
    RankedResult,
    RerankRequest,
    RerankResponse,
    ResponseMetadata,
    ToolCall,
    Usage,
    validate_chat_capabilities,
)

T = TypeVar("T")

_FINISH_REASONS = {
    "COMPLETE": FinishReason.COMPLETE,
    "TOOL_CALL": FinishReason.TOOL_CALL,
    "MAX_TOKENS": FinishReason.MAX_TOKENS,
    "STOP_SEQUENCE": FinishReason.STOP_SEQUENCE,
    "ERROR": FinishReason.ERROR,
    "TIMEOUT": FinishReason.ERROR,
}
_TRANSIENT_ERRORS = (
    cohere.TooManyRequestsError,
    cohere.GatewayTimeoutError,
    cohere.InternalServerError,
    cohere.ServiceUnavailableError,
    httpx.TimeoutException,
    httpx.NetworkError,
)


def _usage(value: Any) -> Usage:
    if value is None:
        return Usage()
    tokens = getattr(value, "tokens", None)
    billed = getattr(value, "billed_units", None)
    return Usage(
        input_tokens=_integer(getattr(tokens, "input_tokens", None)),
        output_tokens=_integer(getattr(tokens, "output_tokens", None)),
        billed_input_tokens=_integer(getattr(billed, "input_tokens", None)),
        billed_output_tokens=_integer(getattr(billed, "output_tokens", None)),
        search_units=getattr(billed, "search_units", None),
    )


def _integer(value: float | None) -> int | None:
    return int(value) if value is not None else None


def _content_text(content: Any) -> str:
    if isinstance(content, str):
        return content
    return "".join(
        item.text
        for item in (content or [])
        if getattr(item, "type", None) == "text" and getattr(item, "text", None)
    )


def _tool_call(value: Any) -> ToolCall:
    function = getattr(value, "function", None)
    if function is None or not function.name:
        raise ModelError(
            "Cohere returned a tool call without a function name",
            code="malformed_provider_response",
            provider="cohere",
        )
    try:
        arguments = json.loads(function.arguments or "{}")
    except json.JSONDecodeError as error:
        raise ModelError(
            "Cohere returned invalid JSON tool arguments",
            code="malformed_provider_response",
            provider="cohere",
        ) from error
    if not isinstance(arguments, dict):
        raise ModelError(
            "Cohere returned non-object tool arguments",
            code="malformed_provider_response",
            provider="cohere",
        )
    return ToolCall(id=value.id, name=function.name, arguments=arguments)


def _citation(value: Any) -> Citation:
    source_ids: list[str] = []
    tool_call_ids: list[str] = []
    for source in value.sources or []:
        source_id = getattr(source, "id", None)
        if not source_id:
            continue
        if getattr(source, "type", None) == "tool":
            tool_call_ids.append(source_id.split(":", maxsplit=1)[0])
        else:
            source_ids.append(source_id)
    return Citation(
        start=value.start or 0,
        end=value.end or 0,
        text=value.text or "",
        source_ids=source_ids,
        tool_call_ids=tool_call_ids,
    )


def _messages(request: ChatRequest) -> list[dict[str, Any]]:
    mapped: list[dict[str, Any]] = []
    for message in request.messages:
        if message.role is MessageRole.TOOL:
            for result in message.tool_results:
                mapped.append(
                    {
                        "role": "tool",
                        "tool_call_id": result.tool_call_id,
                        "content": [
                            {
                                "type": "document",
                                "document": {
                                    "data": json.dumps(
                                        {"content": result.content, "is_error": result.is_error}
                                    )
                                },
                            }
                        ],
                    }
                )
            continue
        item: dict[str, Any] = {"role": message.role.value, "content": message.content}
        if message.tool_calls:
            item["tool_calls"] = [
                {
                    "id": call.id,
                    "type": "function",
                    "function": {
                        "name": call.name,
                        "arguments": json.dumps(call.arguments, separators=(",", ":")),
                    },
                }
                for call in message.tool_calls
            ]
        mapped.append(item)
    return mapped


def _chat_arguments(request: ChatRequest, model: str) -> dict[str, Any]:
    arguments: dict[str, Any] = {"model": model, "messages": _messages(request)}
    if request.tools:
        arguments["tools"] = [
            {
                "type": "function",
                "function": {
                    "name": tool.name,
                    "description": tool.description,
                    "parameters": tool.input_schema,
                },
            }
            for tool in request.tools
        ]
    if request.documents:
        arguments["documents"] = [
            {"id": document.id, "data": {"text": document.text, **document.metadata}}
            for document in request.documents
        ]
    if request.response_schema is not None:
        arguments["response_format"] = {
            "type": "json_object",
            "json_schema": request.response_schema,
        }
    if request.max_tokens is not None:
        arguments["max_tokens"] = request.max_tokens
    if request.temperature is not None:
        arguments["temperature"] = request.temperature
    return arguments


def _provider_error(error: Exception) -> ModelError:
    retryable = isinstance(error, _TRANSIENT_ERRORS)
    if isinstance(error, cohere.TooManyRequestsError):
        code = "rate_limit"
    elif isinstance(error, (cohere.GatewayTimeoutError, httpx.TimeoutException)):
        code = "timeout"
    elif retryable:
        code = "provider_unavailable"
    elif isinstance(error, (cohere.InvalidTokenError, cohere.UnauthorizedError)):
        code = "authentication"
    elif isinstance(error, (cohere.BadRequestError, cohere.UnprocessableEntityError)):
        code = "invalid_request"
    else:
        code = "provider_error"
    return ModelError(
        f"Cohere request failed ({code})",
        code=code,
        retryable=retryable,
        provider="cohere",
        request_id=getattr(error, "request_id", None),
    )


class _CohereBase:
    def __init__(
        self,
        client: Any,
        *,
        model: str,
        max_retries: int = 2,
        include_raw_responses: bool = False,
    ) -> None:
        self.client = client
        self.model = model
        self.max_retries = max_retries
        self.include_raw_responses = include_raw_responses

    async def _retry(self, operation: Callable[[], Awaitable[T]]) -> T:
        for attempt in range(self.max_retries + 1):
            try:
                return await operation()
            except Exception as error:
                mapped = _provider_error(error)
                if not mapped.retryable or attempt == self.max_retries:
                    raise mapped from error
                await asyncio.sleep(min(0.1 * (2**attempt), 1.0))
        raise AssertionError("retry loop did not return or raise")

    def _metadata(
        self,
        response: Any,
        *,
        logical_call_id: str | None,
        latency_ms: float,
    ) -> ResponseMetadata:
        raw = None
        if self.include_raw_responses and hasattr(response, "model_dump"):
            raw = response.model_dump(mode="json")
        return ResponseMetadata(
            provider="cohere",
            model=self.model,
            request_id=getattr(response, "id", None),
            logical_call_id=logical_call_id,
            latency_ms=latency_ms,
            raw_response=raw,
        )


class CohereChatModel(_CohereBase):
    name = "cohere-chat-v2"
    capabilities = ModelCapabilities(
        tools=True,
        citations=True,
        structured_output=True,
        reasoning=True,
        vision=False,
        streaming=True,
    )

    def _response(
        self,
        response: Any,
        request: ChatRequest,
        latency_ms: float,
    ) -> ChatResponse:
        text = _content_text(response.message.content)
        structured_output = None
        if request.response_schema is not None and text:
            try:
                structured_output = json.loads(text)
            except json.JSONDecodeError as error:
                raise ModelError(
                    "Cohere returned invalid structured JSON",
                    code="malformed_provider_response",
                    provider="cohere",
                    request_id=response.id,
                ) from error
        return ChatResponse(
            message=Message(
                role=MessageRole.ASSISTANT,
                content=text,
                tool_calls=[_tool_call(call) for call in (response.message.tool_calls or [])],
            ),
            citations=[_citation(item) for item in (response.message.citations or [])],
            finish_reason=_FINISH_REASONS.get(response.finish_reason, FinishReason.UNKNOWN),
            usage=_usage(response.usage),
            metadata=self._metadata(
                response,
                logical_call_id=request.logical_call_id,
                latency_ms=latency_ms,
            ),
            structured_output=structured_output,
        )

    async def chat(self, request: ChatRequest) -> ChatResponse:
        validate_chat_capabilities(self, request.required_capabilities)
        arguments = _chat_arguments(request, self.model)
        started = time.perf_counter()
        response = await self._retry(lambda: self.client.chat(**arguments))
        return self._response(response, request, (time.perf_counter() - started) * 1000)

    async def stream(self, request: ChatRequest) -> AsyncIterator[ChatStreamEvent]:
        validate_chat_capabilities(self, request.required_capabilities)
        arguments = _chat_arguments(request, self.model)
        text_parts: list[str] = []
        citations: list[Citation] = []
        tool_parts: dict[int, dict[str, str]] = {}
        started = time.perf_counter()
        yielded = False
        for attempt in range(self.max_retries + 1):
            try:
                async for event in self.client.chat_stream(**arguments):
                    event_type = event.type
                    if event_type == "message-start":
                        yielded = True
                        yield ChatStreamEvent(type="message-start")
                    elif event_type == "content-delta":
                        content = event.delta.message.content
                        fragment = content.text if content else ""
                        text_parts.append(fragment)
                        yielded = True
                        yield ChatStreamEvent(type="text-delta", text=fragment)
                    elif event_type == "citation-start":
                        citation = _citation(event.delta.message.citations)
                        citations.append(citation)
                        yielded = True
                        yield ChatStreamEvent(type="citation", citation=citation)
                    elif event_type == "tool-call-start":
                        call = event.delta.message.tool_calls
                        tool_parts[event.index or 0] = {
                            "id": call.id,
                            "name": call.function.name or "",
                            "arguments": call.function.arguments or "",
                        }
                    elif event_type == "tool-call-delta":
                        call = event.delta.message.tool_calls
                        part = tool_parts.setdefault(event.index or 0, {"id": "", "name": "", "arguments": ""})
                        function = call.function
                        if function:
                            part["name"] += function.name or ""
                            part["arguments"] += function.arguments or ""
                    elif event_type == "tool-call-end":
                        part = tool_parts[event.index or 0]
                        call = _tool_call(
                            cohere.ToolCallV2(
                                id=part["id"],
                                function=cohere.ToolCallV2Function(
                                    name=part["name"],
                                    arguments=part["arguments"],
                                ),
                            )
                        )
                        yielded = True
                        yield ChatStreamEvent(type="tool-call", tool_call=call)
                    elif event_type == "message-end":
                        delta = event.delta
                        response = ChatResponse(
                            message=Message(
                                role=MessageRole.ASSISTANT,
                                content="".join(text_parts),
                                tool_calls=[
                                    _tool_call(
                                        cohere.ToolCallV2(
                                            id=part["id"],
                                            function=cohere.ToolCallV2Function(
                                                name=part["name"],
                                                arguments=part["arguments"],
                                            ),
                                        )
                                    )
                                    for part in tool_parts.values()
                                ],
                            ),
                            citations=citations,
                            finish_reason=_FINISH_REASONS.get(
                                delta.finish_reason, FinishReason.UNKNOWN
                            ),
                            usage=_usage(delta.usage),
                            metadata=ResponseMetadata(
                                provider="cohere",
                                model=self.model,
                                request_id=event.id,
                                logical_call_id=request.logical_call_id,
                                latency_ms=(time.perf_counter() - started) * 1000,
                            ),
                        )
                        yielded = True
                        yield ChatStreamEvent(type="message-end", response=response)
                return
            except Exception as error:
                mapped = _provider_error(error)
                if yielded or not mapped.retryable or attempt == self.max_retries:
                    raise mapped from error
                await asyncio.sleep(min(0.1 * (2**attempt), 1.0))


class CohereEmbeddingModel(_CohereBase):
    name = "cohere-embed-v2"

    async def embed(self, request: EmbeddingRequest) -> EmbeddingResponse:
        started = time.perf_counter()
        response = await self._retry(
            lambda: self.client.embed(
                model=self.model,
                texts=request.texts,
                input_type=request.input_type.value,
                embedding_types=["float"],
            )
        )
        vectors = response.embeddings.float_
        if vectors is None:
            raise ModelError(
                "Cohere did not return float embeddings",
                code="malformed_provider_response",
                provider="cohere",
                request_id=response.id,
            )
        return EmbeddingResponse(
            vectors=vectors,
            usage=_usage(response.meta),
            metadata=self._metadata(
                response,
                logical_call_id=request.logical_call_id,
                latency_ms=(time.perf_counter() - started) * 1000,
            ),
        )


class CohereRerankModel(_CohereBase):
    name = "cohere-rerank-v2"

    async def rerank(self, request: RerankRequest) -> RerankResponse:
        started = time.perf_counter()
        arguments: dict[str, Any] = {
            "model": self.model,
            "query": request.query,
            "documents": [document.text for document in request.documents],
        }
        if request.top_n is not None:
            arguments["top_n"] = request.top_n
        response = await self._retry(lambda: self.client.rerank(**arguments))
        return RerankResponse(
            results=[
                RankedResult(
                    index=item.index,
                    relevance_score=item.relevance_score,
                    document=request.documents[item.index],
                )
                for item in response.results
            ],
            usage=_usage(response.meta),
            metadata=self._metadata(
                response,
                logical_call_id=request.logical_call_id,
                latency_ms=(time.perf_counter() - started) * 1000,
            ),
        )
