from datetime import UTC, datetime
from pathlib import Path

import pytest

from highland.models.scripted import ScriptedChatModel
from highland.runtime.approvals import ApprovalStore
from highland.runtime.mcp import NormalizedToolResult
from highland.runtime.policy import RunScope, ToolMode, ToolPolicy, ValidatedToolCall
from highland.workflows import (
    ApprovalNode,
    NodeInput,
    ToolNode,
    TriggerNode,
    WorkflowDefinition,
    WorkflowEdge,
    WorkflowRepository,
)
from highland.workflows.executor import WorkflowExecutor, WorkflowRunRepository, WorkflowRunStatus
from highland.workflows.schedules import WorkflowScheduleRepository


class WriteTools:
    def __init__(self) -> None:
        self.calls = 0

    def validate(self, name, arguments, **_):
        return ValidatedToolCall(
            qualified_name=name,
            arguments=arguments,
            policy=ToolPolicy(
                mode=ToolMode.WRITE, approval_required=True, idempotent_with_key=True
            ),
            idempotency_key="stable",
        )

    async def execute_approved(self, call):
        self.calls += 1
        return NormalizedToolResult(connector="pulse", tool=call.qualified_name, content="posted")


def workflow() -> WorkflowDefinition:
    return WorkflowDefinition(
        id="wf_scheduled",
        name="Scheduled",
        nodes=[
            TriggerNode(id="start", name="Start"),
            ApprovalNode(id="approve", name="Approve", tool_node_id="post", reason="Post update"),
            ToolNode(
                id="post",
                name="Post",
                tool="pulse__post",
                write=True,
                arguments={"message": NodeInput(value="Ready")},
            ),
        ],
        edges=[
            WorkflowEdge(source="start", target="approve"),
            WorkflowEdge(source="approve", target="post"),
        ],
    )


@pytest.mark.asyncio
async def test_write_waits_for_durable_approval_and_restart_does_not_repeat(tmp_path: Path) -> None:
    tools = WriteTools()
    approvals = ApprovalStore(tmp_path / "approvals")
    runs = WorkflowRunRepository(tmp_path / "runs")
    executor = WorkflowExecutor(
        model=ScriptedChatModel([]),
        tools=tools,
        repository=runs,
        approvals=approvals,
    )
    paused = await executor.run(workflow(), run_id="run_write", scope=RunScope())
    assert paused.status is WorkflowRunStatus.PAUSED
    assert tools.calls == 0
    approval_id = paused.nodes["approve"].output["approval_id"]
    approvals.decide(approval_id, approve=True)
    completed = await executor.run(workflow(), run_id="run_write")
    assert completed.status is WorkflowRunStatus.COMPLETED
    assert tools.calls == 1
    restarted = await executor.run(workflow(), run_id="run_write")
    assert restarted.status is WorkflowRunStatus.COMPLETED
    assert tools.calls == 1


def test_only_published_versions_schedule_and_due_runs_are_claimed_once(tmp_path: Path) -> None:
    workflows = WorkflowRepository(tmp_path / "workflows")
    workflows.save_draft(workflow())
    published = workflows.publish("wf_scheduled")
    schedules = WorkflowScheduleRepository(tmp_path / "schedules")
    due = datetime(2026, 1, 1, tzinfo=UTC)
    schedule = schedules.create(published, interval_seconds=3600, start_at=due)

    first = schedules.claim_due(schedule.id, now=due)
    assert first is not None
    assert first[1] == due
    assert schedules.claim_due(schedule.id, now=due) is None
