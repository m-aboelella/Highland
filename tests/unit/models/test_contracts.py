from __future__ import annotations

import pytest

from highland.models.contracts import (
    ChatRequest,
    ChatResponse,
    Citation,
    FinishReason,
    Message,
    MessageRole,
    ModelCapabilities,
    ResponseMetadata,
    ToolCall,
    UnsupportedCapabilityError,
    Usage,
    validate_chat_capabilities,
)


def test_chat_contract_round_trip_preserves_tool_calls_and_citations() -> None:
    response = ChatResponse(
        message=Message(
            role=MessageRole.ASSISTANT,
            content="Ticket 4182 is open.",
            tool_calls=[
                ToolCall(
                    id="call_1",
                    name="support.get_ticket",
                    arguments={"ticket_id": "tkt_4182", "include_comments": True},
                )
            ],
        ),
        citations=[
            Citation(
                start=0,
                end=11,
                text="Ticket 4182",
                source_ids=["tkt_4182"],
                tool_call_ids=["call_1"],
            )
        ],
        finish_reason=FinishReason.TOOL_CALL,
        usage=Usage(input_tokens=12, output_tokens=8),
        metadata=ResponseMetadata(
            provider="test",
            model="script",
            request_id="req_1",
            raw_response={"generation_id": "gen_1"},
        ),
    )

    restored = ChatResponse.model_validate_json(response.model_dump_json())

    assert restored == response
    assert restored.message.tool_calls[0].arguments["include_comments"] is True
    assert restored.citations[0].tool_call_ids == ["call_1"]
    assert restored.usage.total_tokens == 20


def test_unsupported_required_capability_fails_explicitly() -> None:
    class TextOnlyModel:
        name = "text-only"
        capabilities = ModelCapabilities()

    with pytest.raises(UnsupportedCapabilityError, match="tools, citations") as caught:
        validate_chat_capabilities(
            TextOnlyModel(),  # type: ignore[arg-type]
            ModelCapabilities(tools=True, citations=True),
        )

    assert caught.value.code == "unsupported_capability"


def test_request_round_trip_preserves_capability_requirements() -> None:
    request = ChatRequest(
        messages=[Message(role=MessageRole.USER, content="Investigate")],
        required_capabilities=ModelCapabilities(reasoning=True, streaming=True),
    )

    restored = ChatRequest.model_validate(request.model_dump(mode="json"))

    assert restored.required_capabilities.reasoning
    assert restored.required_capabilities.streaming
