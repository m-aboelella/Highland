import pytest

from highland.models.contracts import (
    ChatResponse,
    FinishReason,
    Message,
    MessageRole,
    ToolDefinition,
)
from highland.models.scripted import ScriptedChatModel, simulated_metadata
from highland.runtime.policy import ToolMode, ToolPolicy
from highland.workflows.planner import WorkflowPlanner


def response(output: object) -> ChatResponse:
    return ChatResponse(
        message=Message(role=MessageRole.ASSISTANT),
        finish_reason=FinishReason.COMPLETE,
        metadata=simulated_metadata("workflow-planner"),
        structured_output=output,
    )


def plan(*, approved: bool = True) -> dict[str, object]:
    nodes: list[dict[str, object]] = [{"kind": "trigger", "id": "start", "name": "Start"}]
    edges: list[dict[str, object]] = []
    if approved:
        nodes.append(
            {
                "kind": "approval",
                "id": "approve",
                "name": "Approve",
                "tool_node_id": "post",
                "reason": "External write",
            }
        )
        edges.append({"source": "start", "target": "approve"})
        source = "approve"
    else:
        source = "start"
    nodes.append(
        {
            "kind": "tool",
            "id": "post",
            "name": "Post",
            "tool": "pulse__post",
            "write": True,
        }
    )
    edges.append({"source": source, "target": "post"})
    return {
        "workflow": {"id": "wf_post", "name": "Post update", "nodes": nodes, "edges": edges},
        "rationale": "Post a reviewed update.",
    }


@pytest.mark.asyncio
async def test_planner_uses_tools_repairs_invalid_plan_and_requires_review() -> None:
    model = ScriptedChatModel(
        [response(plan(approved=False)), response(plan())], model="workflow-planner"
    )
    tool = ToolDefinition(name="pulse__post", description="Post", input_schema={"type": "object"})
    draft = await WorkflowPlanner(
        model,
        tools=[tool],
        policies={
            "pulse__post": ToolPolicy(
                mode=ToolMode.WRITE, approval_required=True, idempotent_with_key=True
            )
        },
    ).draft("Post a customer update")

    assert draft.requires_human_review is True
    assert draft.planner.repair_attempted is True
    assert draft.planner.model == "workflow-planner"
    assert len(model.requests) == 2
    assert model.requests[0].tools == [tool]
    assert model.requests[0].response_schema
    assert "Validation error" in model.requests[1].messages[-1].content
