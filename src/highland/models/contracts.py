from __future__ import annotations

from collections.abc import AsyncIterator
from enum import StrEnum
from typing import Literal, Protocol, runtime_checkable

from pydantic import BaseModel, ConfigDict, Field, JsonValue


class ContractModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class MessageRole(StrEnum):
    SYSTEM = "system"
    USER = "user"
    ASSISTANT = "assistant"
    TOOL = "tool"


class FinishReason(StrEnum):
    COMPLETE = "complete"
    TOOL_CALL = "tool_call"
    MAX_TOKENS = "max_tokens"
    STOP_SEQUENCE = "stop_sequence"
    ERROR = "error"
    UNKNOWN = "unknown"


class InputType(StrEnum):
    SEARCH_DOCUMENT = "search_document"
    SEARCH_QUERY = "search_query"
    CLASSIFICATION = "classification"
    CLUSTERING = "clustering"


class ToolDefinition(ContractModel):
    name: str
    description: str
    input_schema: dict[str, JsonValue]


class ToolCall(ContractModel):
    id: str
    name: str
    arguments: dict[str, JsonValue]


class ToolResult(ContractModel):
    tool_call_id: str
    content: str
    is_error: bool = False


class Message(ContractModel):
    role: MessageRole
    content: str = ""
    tool_calls: list[ToolCall] = Field(default_factory=list)
    tool_results: list[ToolResult] = Field(default_factory=list)


class Document(ContractModel):
    id: str
    text: str
    metadata: dict[str, JsonValue] = Field(default_factory=dict)


class Citation(ContractModel):
    start: int
    end: int
    text: str
    source_ids: list[str] = Field(default_factory=list)
    tool_call_ids: list[str] = Field(default_factory=list)


class Usage(ContractModel):
    input_tokens: int | None = Field(default=None, ge=0)
    output_tokens: int | None = Field(default=None, ge=0)
    billed_input_tokens: int | None = Field(default=None, ge=0)
    billed_output_tokens: int | None = Field(default=None, ge=0)
    search_units: float | None = Field(default=None, ge=0)

    @property
    def total_tokens(self) -> int | None:
        values = (self.input_tokens, self.output_tokens)
        return None if all(value is None for value in values) else sum(value or 0 for value in values)


class ResponseMetadata(ContractModel):
    provider: str
    model: str
    request_id: str | None = None
    logical_call_id: str | None = None
    latency_ms: float | None = Field(default=None, ge=0)
    simulated: bool = False
    raw_response: dict[str, JsonValue] | None = None


class ModelCapabilities(ContractModel):
    tools: bool = False
    citations: bool = False
    structured_output: bool = False
    reasoning: bool = False
    vision: bool = False
    streaming: bool = False

    def require(self, required: ModelCapabilities, *, provider: str) -> None:
        missing = [
            field
            for field in type(self).model_fields
            if getattr(required, field) and not getattr(self, field)
        ]
        if missing:
            raise UnsupportedCapabilityError(provider=provider, capabilities=missing)


class ChatRequest(ContractModel):
    messages: list[Message]
    tools: list[ToolDefinition] = Field(default_factory=list)
    documents: list[Document] = Field(default_factory=list)
    response_schema: dict[str, JsonValue] | None = None
    max_tokens: int | None = Field(default=None, gt=0)
    temperature: float | None = Field(default=None, ge=0)
    required_capabilities: ModelCapabilities = Field(default_factory=ModelCapabilities)
    logical_call_id: str | None = None


class ChatResponse(ContractModel):
    message: Message
    citations: list[Citation] = Field(default_factory=list)
    finish_reason: FinishReason
    usage: Usage = Field(default_factory=Usage)
    metadata: ResponseMetadata
    structured_output: JsonValue = None


class ChatStreamEvent(ContractModel):
    type: Literal[
        "message-start",
        "text-delta",
        "tool-call",
        "citation",
        "message-end",
        "error",
    ]
    text: str | None = None
    tool_call: ToolCall | None = None
    citation: Citation | None = None
    response: ChatResponse | None = None


class EmbeddingRequest(ContractModel):
    texts: list[str] = Field(min_length=1)
    input_type: InputType
    logical_call_id: str | None = None


class EmbeddingResponse(ContractModel):
    vectors: list[list[float]]
    usage: Usage = Field(default_factory=Usage)
    metadata: ResponseMetadata


class RerankRequest(ContractModel):
    query: str
    documents: list[Document] = Field(min_length=1)
    top_n: int | None = Field(default=None, gt=0)
    logical_call_id: str | None = None


class RankedResult(ContractModel):
    index: int = Field(ge=0)
    relevance_score: float
    document: Document


class RerankResponse(ContractModel):
    results: list[RankedResult]
    usage: Usage = Field(default_factory=Usage)
    metadata: ResponseMetadata


class ModelError(Exception):
    """Provider-neutral failure safe to surface at the runtime boundary."""

    def __init__(
        self,
        message: str,
        *,
        code: str,
        retryable: bool = False,
        provider: str | None = None,
        request_id: str | None = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.retryable = retryable
        self.provider = provider
        self.request_id = request_id


class UnsupportedCapabilityError(ModelError):
    def __init__(self, *, provider: str, capabilities: list[str]) -> None:
        self.capabilities = tuple(capabilities)
        super().__init__(
            f"{provider} does not support required capabilities: {', '.join(capabilities)}",
            code="unsupported_capability",
            provider=provider,
        )


@runtime_checkable
class ChatModel(Protocol):
    name: str
    capabilities: ModelCapabilities

    async def chat(self, request: ChatRequest) -> ChatResponse: ...

    def stream(self, request: ChatRequest) -> AsyncIterator[ChatStreamEvent]: ...


@runtime_checkable
class EmbeddingModel(Protocol):
    name: str

    async def embed(self, request: EmbeddingRequest) -> EmbeddingResponse: ...


@runtime_checkable
class RerankModel(Protocol):
    name: str

    async def rerank(self, request: RerankRequest) -> RerankResponse: ...


def validate_chat_capabilities(model: ChatModel, required: ModelCapabilities) -> None:
    model.capabilities.require(required, provider=model.name)
