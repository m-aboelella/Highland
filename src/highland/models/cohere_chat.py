from __future__ import annotations

import asyncio
import json
import time
from collections.abc import AsyncIterator
from typing import Any

import cohere

from .cohere_mapping import (
    _FINISH_REASONS,
    _chat_arguments,
    _citation,
    _CohereBase,
    _content_text,
    _provider_error,
    _tool_call,
    _usage,
)
from .contracts import (
    ChatRequest,
    ChatResponse,
    ChatStreamEvent,
    Citation,
    FinishReason,
    Message,
    MessageRole,
    ModelCapabilities,
    ModelError,
    ResponseMetadata,
    validate_chat_capabilities,
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
