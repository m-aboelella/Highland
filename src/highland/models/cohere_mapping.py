from __future__ import annotations

import asyncio
import json
from collections.abc import Awaitable, Callable
from typing import Any, TypeVar

import cohere
import httpx

from .contracts import (
    ChatRequest,
    Citation,
    FinishReason,
    MessageRole,
    ModelError,
    ResponseMetadata,
    ToolCall,
    Usage,
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

# Cohere Structured Outputs supports the shape-bearing parts of JSON Schema,
# but not every validation keyword emitted by Pydantic. Highland still applies
# the complete Pydantic model after generation, so these constraints remain
# enforced locally even though they cannot be sent to the provider.
_UNSUPPORTED_RESPONSE_SCHEMA_KEYS = {
    "exclusiveMaximum",
    "exclusiveMinimum",
    "maxItems",
    "maxLength",
    "maximum",
    "minItems",
    "minLength",
    "minimum",
    "uniqueItems",
}
_UNSUPPORTED_PATTERN_TOKENS = ("^", "$", "?=", "?!")


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


def _cohere_response_schema(value: Any) -> Any:
    if isinstance(value, dict):
        normalized = {
            key: _cohere_response_schema(item)
            for key, item in value.items()
            if key not in _UNSUPPORTED_RESPONSE_SCHEMA_KEYS
            and not (
                key == "pattern"
                and isinstance(item, str)
                and any(token in item for token in _UNSUPPORTED_PATTERN_TOKENS)
            )
        }
        properties = normalized.get("properties")
        if (
            normalized.get("type") == "object"
            and isinstance(properties, dict)
            and properties
            and not normalized.get("required")
        ):
            normalized["required"] = [next(iter(properties))]
        return normalized
    if isinstance(value, list):
        return [_cohere_response_schema(item) for item in value]
    return value


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
            "json_schema": _cohere_response_schema(request.response_schema),
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
    detail = _provider_error_detail(error) if code == "invalid_request" else None
    message = f"Cohere request failed ({code})"
    if detail:
        message = f"{message}: {detail}"
    headers = getattr(error, "headers", None)
    request_id = getattr(error, "request_id", None)
    if request_id is None and isinstance(headers, dict):
        request_id = headers.get("x-request-id")
    return ModelError(
        message,
        code=code,
        retryable=retryable,
        provider="cohere",
        request_id=request_id,
    )


def _provider_error_detail(error: Exception) -> str | None:
    body = getattr(error, "body", None)
    if isinstance(body, dict):
        detail = body.get("message") or body.get("detail")
    elif isinstance(body, str):
        detail = body
    else:
        detail = None
    if not isinstance(detail, str) or not detail.strip():
        return None
    return " ".join(detail.split())[:500]


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
