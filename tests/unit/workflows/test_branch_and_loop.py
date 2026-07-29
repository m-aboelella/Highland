from pathlib import Path

import pytest
from pydantic import ValidationError

from highland.models.contracts import ChatResponse, FinishReason, Message, MessageRole
from highland.models.scripted import ScriptedChatModel, simulated_metadata
from highland.runtime.policy import RunScope
from highland.workflows import (
    GenerateNode,
    NodeInput,
    OutputReference,
    TriggerNode,
    WorkflowDefinition,
    WorkflowEdge,
)
from highland.workflows.executor import WorkflowExecutor, WorkflowRunRepository


def response(value: object) -> ChatResponse:
    return ChatResponse(
        message=Message(role=MessageRole.ASSISTANT),
        finish_reason=FinishReason.COMPLETE,
        metadata=simulated_metadata("workflow"),
        structured_output=value,
    )


@pytest.mark.asyncio
async def test_safe_branch_and_bounded_customer_loop(tmp_path: Path) -> None:
    workflow = WorkflowDefinition(
        id="wf_loop",
        name="Health",
        nodes=[
            TriggerNode(id="start", name="Start"),
            GenerateNode(
                id="customers",
                name="Customers",
                prompt=NodeInput(value="List customers"),
                response_schema={"type": "array"},
            ),
            GenerateNode(
                id="classify",
                name="Classify",
                prompt=NodeInput(
                    reference=OutputReference(node_id="$item", path="customer_id")
                ),
                response_schema={"type": "object"},
            ),
            GenerateNode(
                id="alert",
                name="Alert",
                prompt=NodeInput(value="Alert"),
            ),
        ],
        edges=[
            WorkflowEdge(source="start", target="customers"),
            WorkflowEdge(
                source="customers",
                target="classify",
                loop_over=OutputReference(node_id="customers"),
                max_iterations=2,
            ),
            WorkflowEdge(source="classify", target="alert", condition='classify.0.output.risk == "high"'),
        ],
    )
    model = ScriptedChatModel(
        [
            response(
                [
                    {"customer_id": "cust_a"},
                    {"customer_id": "cust_b"},
                ]
            ),
            response({"risk": "high"}),
            response({"risk": "low"}),
            ChatResponse(
                message=Message(role=MessageRole.ASSISTANT, content="Alert"),
                finish_reason=FinishReason.COMPLETE,
                metadata=simulated_metadata("workflow"),
            ),
        ]
    )
    run = await WorkflowExecutor(
        model=model,
        tools=object(),
        repository=WorkflowRunRepository(tmp_path),
    ).run(
        workflow,
        run_id="run_loop",
        scope=RunScope(allowed_customers=frozenset({"cust_a", "cust_b"})),
    )
    assert len(run.nodes["classify"].output) == 2
    assert [item["record"]["customer_id"] for item in run.nodes["classify"].output] == [
        "cust_a",
        "cust_b",
    ]
    assert run.nodes["alert"].output == "Alert"
    assert run.model_calls == 4


def test_rejects_code_expressions_and_caps_generated_collections(tmp_path: Path) -> None:
    with pytest.raises(ValidationError, match="unsafe"):
        WorkflowDefinition(
            name="unsafe",
            nodes=[
                TriggerNode(id="start", name="Start"),
                GenerateNode(id="x", name="X", prompt=NodeInput(value="x")),
            ],
            edges=[WorkflowEdge(source="start", target="x", condition="__import__('os').system('x')")],
        )
