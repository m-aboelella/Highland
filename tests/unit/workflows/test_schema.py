from pathlib import Path

import pytest
from pydantic import ValidationError

from highland.workflows import (
    ApprovalNode,
    NodeInput,
    OutputReference,
    ToolNode,
    TriggerNode,
    WorkflowDefinition,
    WorkflowEdge,
    WorkflowRepository,
)


def manual(value: object) -> NodeInput:
    return NodeInput(value=value)


def valid_workflow() -> WorkflowDefinition:
    return WorkflowDefinition(
        id="wf_notify",
        name="Notify customers",
        nodes=[
            TriggerNode(id="start", name="Manual start"),
            ApprovalNode(id="approve", name="Review", tool_node_id="notify", reason="External write"),
            ToolNode(
                id="notify",
                name="Post update",
                tool="pulse__post_customer_update",
                write=True,
                arguments={"message": manual("Ready")},
            ),
        ],
        edges=[
            WorkflowEdge(source="start", target="approve"),
            WorkflowEdge(source="approve", target="notify"),
        ],
    )


def test_schema_rejects_unreachable_cycles_missing_references_and_unapproved_writes() -> None:
    with pytest.raises(ValidationError, match="unreachable"):
        WorkflowDefinition(
            name="bad",
            nodes=[
                TriggerNode(id="start", name="Start"),
                ToolNode(id="orphan", name="Orphan", tool="x"),
            ],
        )
    with pytest.raises(ValidationError, match="cycle"):
        WorkflowDefinition(
            name="cycle",
            nodes=[
                TriggerNode(id="start", name="Start"),
                ToolNode(id="a", name="A", tool="x"),
                ToolNode(id="b", name="B", tool="x"),
            ],
            edges=[
                WorkflowEdge(source="start", target="a"),
                WorkflowEdge(source="a", target="b"),
                WorkflowEdge(source="b", target="a"),
            ],
        )
    with pytest.raises(ValidationError, match="missing output"):
        WorkflowDefinition(
            name="reference",
            nodes=[
                TriggerNode(id="start", name="Start"),
                ToolNode(
                    id="tool",
                    name="Tool",
                    tool="x",
                    inputs={"x": NodeInput(reference=OutputReference(node_id="missing"))},
                ),
            ],
            edges=[WorkflowEdge(source="start", target="tool")],
        )
    with pytest.raises(ValidationError, match="requires a preceding approval"):
        WorkflowDefinition(
            name="write",
            nodes=[
                TriggerNode(id="start", name="Start"),
                ToolNode(id="write", name="Write", tool="x", write=True),
            ],
            edges=[WorkflowEdge(source="start", target="write")],
        )


def test_repository_keeps_published_versions_immutable_and_readable(tmp_path: Path) -> None:
    repository = WorkflowRepository(tmp_path)
    draft = repository.save_draft(valid_workflow())
    first = repository.publish(draft.id)
    repository.save_draft(draft.model_copy(update={"description": "edited"}))
    second = repository.publish(draft.id)

    assert first.version == 1
    assert second.version == 2
    assert repository.get_version(draft.id, 1).definition.description == ""
    assert repository.get_version(draft.id, 2).definition.description == "edited"
    assert '"kind": "approval"' in (
        tmp_path / draft.id / "versions" / "1.json"
    ).read_text("utf-8")
