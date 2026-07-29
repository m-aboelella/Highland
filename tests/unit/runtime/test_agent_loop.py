from __future__ import annotations

from pathlib import Path

import pytest

from highland.models.contracts import (
    ChatResponse,
    FinishReason,
    Message,
    MessageRole,
    ToolCall,
    Usage,
)
from highland.models.scripted import ScriptedChatModel, simulated_metadata
from highland.runtime.agent import AgentLoop, AgentProfile, RunRepository, RunStatus
from highland.runtime.mcp import NormalizedToolResult
from highland.runtime.policy import RunScope, ToolMode, ToolPolicy, ValidatedToolCall

PROFILE = Path(__file__).parents[3] / "config" / "agents" / "general.json"


def response(content: str = "", *calls: ToolCall) -> ChatResponse:
    return ChatResponse(
        message=Message(role=MessageRole.ASSISTANT, content=content, tool_calls=list(calls)),
        finish_reason=FinishReason.TOOL_CALL if calls else FinishReason.COMPLETE,
        usage=Usage(input_tokens=2, output_tokens=3),
        metadata=simulated_metadata("test"),
    )


class Tools:
    def __init__(self, *, reject_once: bool = False) -> None:
        self.calls: list[str] = []
        self.reject_once = reject_once

    def model_tools(self) -> list[object]:
        return []

    def validate(self, name, arguments, **_):
        if self.reject_once:
            self.reject_once = False
            from highland.runtime.policy import ToolRejected

            raise ToolRejected("bad arguments", code="invalid_arguments")
        return ValidatedToolCall(
            qualified_name=name,
            arguments=arguments,
            policy=ToolPolicy(mode=ToolMode.READ),
            idempotency_key=None,
        )

    async def execute(self, call):
        self.calls.append(call.qualified_name)
        return NormalizedToolResult(
            connector=call.qualified_name.split("__")[0],
            tool=call.qualified_name,
            content=f"result:{call.qualified_name}",
        )


def loop(tmp_path, steps, tools=None, *, max_steps=None):
    profile = AgentProfile.load(PROFILE)
    if max_steps:
        profile = profile.model_copy(
            update={"budgets": profile.budgets.model_copy(update={"max_steps": max_steps})}
        )
    model = ScriptedChatModel(steps)
    runtime = AgentLoop(model, tools or Tools(), profile, RunRepository(tmp_path))
    return runtime, model


@pytest.mark.asyncio
async def test_no_tool_final_records_usage_and_response(tmp_path) -> None:
    runtime, _ = loop(tmp_path, [response("grounded answer")])
    outcome = await runtime.run(run_id="no-tool", user_message="question")
    assert outcome.status is RunStatus.COMPLETED
    assert outcome.content == "grounded answer"
    assert outcome.usage.total_tokens == 5


@pytest.mark.asyncio
async def test_one_and_multiple_read_tools_are_returned_to_model_and_trace(tmp_path) -> None:
    calls = (
        ToolCall(id="a", name="crm__get_customer", arguments={"customer_id": "c"}),
        ToolCall(id="b", name="support__get_ticket", arguments={"ticket_id": "t"}),
    )
    tools = Tools()
    runtime, model = loop(tmp_path, [response("", *calls), response("done")], tools)
    outcome = await runtime.run(
        run_id="tools",
        user_message="investigate",
        scope=RunScope(allowed_customers=frozenset({"c"})),
    )
    assert outcome.status is RunStatus.COMPLETED
    assert tools.calls == ["crm__get_customer", "support__get_ticket"]
    assert len(model.requests[1].messages[-1].tool_results) == 2
    state = RunRepository(tmp_path).load("tools")
    assert len([event for event in state["events"] if event["type"] == "tool_result"]) == 2


@pytest.mark.asyncio
async def test_invalid_tool_arguments_can_be_corrected(tmp_path) -> None:
    call = ToolCall(id="bad", name="crm__get_customer", arguments={})
    corrected = ToolCall(id="good", name="crm__get_customer", arguments={"customer_id": "c"})
    tools = Tools(reject_once=True)
    runtime, model = loop(
        tmp_path,
        [response("", call), response("", corrected), response("fixed")],
        tools,
    )
    outcome = await runtime.run(run_id="retry", user_message="lookup")
    assert outcome.status is RunStatus.COMPLETED
    assert model.requests[1].messages[-1].tool_results[0].is_error
    assert tools.calls == ["crm__get_customer"]


@pytest.mark.asyncio
async def test_maximum_step_path_is_bounded(tmp_path) -> None:
    call = ToolCall(id="again", name="crm__get_customer", arguments={"customer_id": "c"})
    runtime, _ = loop(tmp_path, [response("", call), response("", call)], max_steps=2)
    outcome = await runtime.run(run_id="bounded", user_message="loop")
    assert outcome.status is RunStatus.MAX_STEPS
