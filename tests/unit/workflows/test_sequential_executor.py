from pathlib import Path

import pytest

from highland.models.contracts import ChatResponse, FinishReason, Message, MessageRole
from highland.models.scripted import ScriptedChatModel, simulated_metadata
from highland.workflows import (
    GenerateNode,
    NodeInput,
    OutputReference,
    TriggerNode,
    WorkflowDefinition,
    WorkflowEdge,
)
from highland.workflows.executor import (
    WorkflowExecutor,
    WorkflowRunRepository,
    WorkflowRunStatus,
)


def generated(text: str) -> ChatResponse:
    return ChatResponse(
        message=Message(role=MessageRole.ASSISTANT, content=text),
        finish_reason=FinishReason.COMPLETE,
        metadata=simulated_metadata("workflow"),
    )


@pytest.mark.asyncio
async def test_executor_persists_inputs_outputs_and_resumes_without_repeating(tmp_path: Path) -> None:
    workflow = WorkflowDefinition(
        id="wf_sequential",
        name="Sequential",
        nodes=[
            TriggerNode(id="start", name="Start"),
            GenerateNode(
                id="first",
                name="First",
                prompt=NodeInput(value="Summarize"),
            ),
            GenerateNode(
                id="second",
                name="Second",
                prompt=NodeInput(
                    reference=OutputReference(node_id="first")
                ),
            ),
        ],
        edges=[
            WorkflowEdge(source="start", target="first"),
            WorkflowEdge(source="first", target="second"),
        ],
    )
    directory = tmp_path / "runs"
    first_model = ScriptedChatModel([generated("durable")])
    failed = await WorkflowExecutor(
        model=first_model,
        tools=object(),
        repository=WorkflowRunRepository(directory),
    ).run(workflow, run_id="run_resume")
    assert failed.status is WorkflowRunStatus.FAILED
    assert failed.nodes["first"].attempts == 1
    assert failed.nodes["second"].attempts == 1

    resumed_model = ScriptedChatModel([generated("complete")])
    resumed = await WorkflowExecutor(
        model=resumed_model,
        tools=object(),
        repository=WorkflowRunRepository(directory),
    ).run(workflow, run_id="run_resume")
    assert resumed.status is WorkflowRunStatus.COMPLETED
    assert resumed.nodes["first"].attempts == 1
    assert resumed.nodes["second"].inputs == {"prompt": "durable"}
    assert resumed.nodes["second"].attempts == 2
    assert len(resumed_model.requests) == 1


@pytest.mark.asyncio
async def test_generate_node_receives_its_named_workflow_inputs(tmp_path: Path) -> None:
    workflow = WorkflowDefinition(
        id="wf_context",
        name="Context",
        nodes=[
            TriggerNode(id="start", name="Start"),
            GenerateNode(
                id="report",
                name="Report",
                inputs={"metrics": NodeInput(value={"p95_ms": 420})},
                prompt=NodeInput(value="Summarize the metrics"),
            ),
        ],
        edges=[WorkflowEdge(source="start", target="report")],
    )
    model = ScriptedChatModel([generated("complete")])

    run = await WorkflowExecutor(
        model=model,
        tools=object(),
        repository=WorkflowRunRepository(tmp_path / "runs"),
    ).run(workflow, run_id="run_context")

    assert run.status is WorkflowRunStatus.COMPLETED
    prompt = model.requests[0].messages[-1].content
    assert prompt.startswith("Summarize the metrics\n\nWorkflow inputs:")
    assert '"p95_ms": 420' in prompt
