from __future__ import annotations

import asyncio
import hashlib
import json
import math
from collections import deque
from collections.abc import AsyncIterator, Iterable
from dataclasses import dataclass
from pathlib import Path

from pydantic import ValidationError

from .contracts import (
    ChatRequest,
    ChatResponse,
    ChatStreamEvent,
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
    Usage,
    validate_chat_capabilities,
)


class UnexpectedModelCall(AssertionError):
    """Raised when a test makes a call that its script did not declare."""


class MalformedScriptedResponse(ModelError):
    def __init__(self, details: str) -> None:
        super().__init__(
            f"Scripted provider produced a malformed response: {details}",
            code="malformed_scripted_response",
            provider="scripted",
        )


@dataclass(frozen=True, slots=True)
class ScriptedFailure:
    code: str
    message: str
    retryable: bool = False
    latency_seconds: float = 0

    @classmethod
    def rate_limit(cls, *, latency_seconds: float = 0) -> ScriptedFailure:
        return cls(
            code="rate_limit",
            message="Simulated provider rate limit",
            retryable=True,
            latency_seconds=latency_seconds,
        )

    @classmethod
    def timeout(cls, *, latency_seconds: float = 0) -> ScriptedFailure:
        return cls(
            code="timeout",
            message="Simulated provider timeout",
            retryable=True,
            latency_seconds=latency_seconds,
        )


@dataclass(frozen=True, slots=True)
class ScriptedChatStep:
    response: ChatResponse | dict[str, object]
    stream_fragments: tuple[str, ...] = ()
    latency_seconds: float = 0


@dataclass(frozen=True, slots=True)
class ScriptedRerankStep:
    scores: tuple[float, ...]
    latency_seconds: float = 0


ChatStep = ScriptedChatStep | ScriptedFailure | ChatResponse | dict[str, object]


def simulated_metadata(model: str, *, request_id: str | None = None) -> ResponseMetadata:
    return ResponseMetadata(
        provider="scripted",
        model=model,
        request_id=request_id,
        simulated=True,
    )


class ScriptedChatModel:
    name = "scripted-chat"
    capabilities = ModelCapabilities(
        tools=True,
        citations=True,
        structured_output=True,
        reasoning=True,
        vision=True,
        streaming=True,
    )

    def __init__(self, steps: Iterable[ChatStep], *, model: str = "scripted-chat") -> None:
        self.model = model
        self.requests: list[ChatRequest] = []
        self._steps = deque(steps)

    def _take_step(self, request: ChatRequest) -> ScriptedChatStep:
        validate_chat_capabilities(self, request.required_capabilities)
        self.requests.append(request.model_copy(deep=True))
        if not self._steps:
            raise UnexpectedModelCall(
                f"Unexpected chat call {len(self.requests)}: scripted response queue is exhausted"
            )
        item = self._steps.popleft()
        if isinstance(item, ScriptedFailure):
            return ScriptedChatStep(
                response={
                    "__failure_code__": item.code,
                    "message": item.message,
                    "retryable": item.retryable,
                },
                latency_seconds=item.latency_seconds,
            )
        if isinstance(item, ChatResponse):
            return ScriptedChatStep(response=item)
        if isinstance(item, dict):
            return ScriptedChatStep(response=item)
        return item

    async def _resolve(self, step: ScriptedChatStep) -> ChatResponse:
        if step.latency_seconds:
            await asyncio.sleep(step.latency_seconds)
        if isinstance(step.response, dict) and "__failure_code__" in step.response:
            raise ModelError(
                str(step.response.get("message", "Simulated provider failure")),
                code=str(step.response["__failure_code__"]),
                retryable=bool(step.response.get("retryable", False)),
                provider="scripted",
            )
        try:
            response = (
                step.response
                if isinstance(step.response, ChatResponse)
                else ChatResponse.model_validate(step.response)
            )
        except ValidationError as error:
            raise MalformedScriptedResponse(str(error)) from error
        return response.model_copy(
            update={
                "metadata": response.metadata.model_copy(
                    update={"provider": "scripted", "model": self.model, "simulated": True}
                )
            },
            deep=True,
        )

    async def chat(self, request: ChatRequest) -> ChatResponse:
        return await self._resolve(self._take_step(request))

    async def stream(self, request: ChatRequest) -> AsyncIterator[ChatStreamEvent]:
        step = self._take_step(request)
        response = await self._resolve(step)
        yield ChatStreamEvent(type="message-start")
        fragments = step.stream_fragments or (response.message.content,)
        for fragment in fragments:
            if fragment:
                yield ChatStreamEvent(type="text-delta", text=fragment)
        for call in response.message.tool_calls:
            yield ChatStreamEvent(type="tool-call", tool_call=call)
        for citation in response.citations:
            yield ChatStreamEvent(type="citation", citation=citation)
        yield ChatStreamEvent(type="message-end", response=response)

    @property
    def remaining_steps(self) -> int:
        return len(self._steps)


class DeterministicEmbeddingModel:
    name = "deterministic-embedding"

    def __init__(self, *, dimensions: int = 32, model: str = "deterministic-embedding") -> None:
        if dimensions <= 0:
            raise ValueError("dimensions must be positive")
        self.dimensions = dimensions
        self.model = model
        self.requests: list[EmbeddingRequest] = []

    def _vector(self, text: str) -> list[float]:
        values: list[float] = []
        counter = 0
        while len(values) < self.dimensions:
            digest = hashlib.sha256(f"{counter}:{text}".encode()).digest()
            values.extend((byte - 127.5) / 127.5 for byte in digest)
            counter += 1
        vector = values[: self.dimensions]
        magnitude = math.sqrt(sum(value * value for value in vector))
        return [value / magnitude for value in vector] if magnitude else vector

    async def embed(self, request: EmbeddingRequest) -> EmbeddingResponse:
        self.requests.append(request.model_copy(deep=True))
        return EmbeddingResponse(
            vectors=[self._vector(text) for text in request.texts],
            metadata=simulated_metadata(self.model),
        )


class ScriptedRerankModel:
    name = "scripted-rerank"

    def __init__(
        self,
        steps: Iterable[ScriptedRerankStep | ScriptedFailure],
        *,
        model: str = "scripted-rerank",
    ) -> None:
        self.model = model
        self.requests: list[RerankRequest] = []
        self._steps = deque(steps)

    async def rerank(self, request: RerankRequest) -> RerankResponse:
        self.requests.append(request.model_copy(deep=True))
        if not self._steps:
            raise UnexpectedModelCall(
                f"Unexpected rerank call {len(self.requests)}: scripted response queue is exhausted"
            )
        step = self._steps.popleft()
        if step.latency_seconds:
            await asyncio.sleep(step.latency_seconds)
        if isinstance(step, ScriptedFailure):
            raise ModelError(
                step.message,
                code=step.code,
                retryable=step.retryable,
                provider="scripted",
            )
        if len(step.scores) != len(request.documents):
            raise MalformedScriptedResponse(
                f"expected {len(request.documents)} rerank scores, got {len(step.scores)}"
            )
        ranked = sorted(enumerate(step.scores), key=lambda item: item[1], reverse=True)
        if request.top_n is not None:
            ranked = ranked[: request.top_n]
        return RerankResponse(
            results=[
                RankedResult(
                    index=index,
                    relevance_score=score,
                    document=request.documents[index],
                )
                for index, score in ranked
            ],
            usage=Usage(search_units=1),
            metadata=simulated_metadata(self.model),
        )


def load_named_chat_script(name: str, *, scripts_dir: Path) -> ScriptedChatModel:
    path = scripts_dir / f"{name}.json"
    payload = json.loads(path.read_text(encoding="utf-8"))
    if payload.get("mode") != "simulated":
        raise ValueError(f"Offline script {path} must declare mode=simulated")
    steps = []
    for index, step in enumerate(payload.get("chat_steps", []), start=1):
        response = ChatResponse(
            message=Message(role=MessageRole.ASSISTANT, content=step["content"]),
            finish_reason=FinishReason.COMPLETE,
            metadata=simulated_metadata(payload.get("model", "offline-demo"), request_id=f"{name}-{index}"),
            structured_output=step.get("structured_output"),
        )
        steps.append(
            ScriptedChatStep(
                response=response,
                stream_fragments=tuple(step.get("stream_fragments", ())),
            )
        )
    return ScriptedChatModel(steps, model=payload.get("model", "offline-demo"))
