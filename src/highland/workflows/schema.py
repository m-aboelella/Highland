from __future__ import annotations

import re
from datetime import UTC, datetime
from enum import StrEnum
from typing import Annotated, Any, Literal
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field, model_validator


def utc_now() -> datetime:
    return datetime.now(UTC)


class WorkflowModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ValueType(StrEnum):
    STRING = "string"
    NUMBER = "number"
    BOOLEAN = "boolean"
    OBJECT = "object"
    LIST = "list"
    ANY = "any"


class OutputReference(WorkflowModel):
    node_id: str
    path: str = ""
    value_type: ValueType = ValueType.ANY


class NodeInput(WorkflowModel):
    value: Any | None = None
    reference: OutputReference | None = None

    @model_validator(mode="after")
    def exactly_one_source(self) -> NodeInput:
        if (self.value is None) == (self.reference is None):
            raise ValueError("an input must define exactly one of value or reference")
        return self


class BaseNode(WorkflowModel):
    id: str = Field(pattern=r"^[A-Za-z][A-Za-z0-9_-]*$")
    name: str
    inputs: dict[str, NodeInput] = Field(default_factory=dict)
    output_type: ValueType = ValueType.ANY


class TriggerNode(BaseNode):
    kind: Literal["trigger"] = "trigger"
    trigger_type: Literal["manual", "schedule"] = "manual"


class RetrieveNode(BaseNode):
    kind: Literal["retrieve"] = "retrieve"
    query: NodeInput
    customer_id: NodeInput | None = None
    output_type: ValueType = ValueType.LIST


class ToolNode(BaseNode):
    kind: Literal["tool"] = "tool"
    tool: str
    arguments: dict[str, NodeInput] = Field(default_factory=dict)
    write: bool = False


class GenerateNode(BaseNode):
    kind: Literal["generate"] = "generate"
    prompt: NodeInput
    response_schema: dict[str, Any] | None = None
    model: str | None = None


class ApprovalNode(BaseNode):
    kind: Literal["approval"] = "approval"
    tool_node_id: str
    reason: str
    output_type: ValueType = ValueType.BOOLEAN


WorkflowNode = Annotated[
    TriggerNode | RetrieveNode | ToolNode | GenerateNode | ApprovalNode,
    Field(discriminator="kind"),
]


class WorkflowEdge(WorkflowModel):
    source: str
    target: str
    condition: str | None = None
    loop_over: OutputReference | None = None
    max_iterations: int = Field(default=100, ge=1, le=1000)


class WorkflowDefinition(WorkflowModel):
    schema_version: int = 1
    id: str = Field(
        default_factory=lambda: f"wf_{uuid4().hex}",
        pattern=r"^wf_[A-Za-z0-9_-]+$",
    )
    name: str
    description: str = ""
    nodes: list[WorkflowNode] = Field(min_length=1)
    edges: list[WorkflowEdge] = Field(default_factory=list)
    created_at: datetime = Field(default_factory=utc_now)
    updated_at: datetime = Field(default_factory=utc_now)

    @model_validator(mode="after")
    def validate_graph(self) -> WorkflowDefinition:
        nodes = {node.id: node for node in self.nodes}
        if len(nodes) != len(self.nodes):
            raise ValueError("workflow node ids must be unique")
        triggers = [node for node in self.nodes if isinstance(node, TriggerNode)]
        if len(triggers) != 1:
            raise ValueError("workflow must contain exactly one trigger")
        incoming: dict[str, list[str]] = {node_id: [] for node_id in nodes}
        outgoing: dict[str, list[str]] = {node_id: [] for node_id in nodes}
        for edge in self.edges:
            if edge.source not in nodes or edge.target not in nodes:
                raise ValueError(f"edge references missing node: {edge.source} -> {edge.target}")
            if edge.source == edge.target:
                raise ValueError("self-referential edges are not allowed")
            outgoing[edge.source].append(edge.target)
            incoming[edge.target].append(edge.source)
            if edge.loop_over and edge.loop_over.node_id not in nodes:
                raise ValueError(f"loop references missing node: {edge.loop_over.node_id}")
            if edge.condition and not _CONDITION.fullmatch(edge.condition.strip()):
                raise ValueError(f"unsafe or invalid branch expression: {edge.condition}")
        if sum(edge.loop_over is not None for edge in self.edges) > 1:
            raise ValueError("workflow supports at most one record loop")
        if sum(edge.condition is not None for edge in self.edges) > 2:
            raise ValueError("workflow supports one conditional branch")
        trigger = triggers[0]
        if incoming[trigger.id]:
            raise ValueError("trigger cannot have incoming edges")
        visited: set[str] = set()
        active: set[str] = set()

        def visit(node_id: str) -> None:
            if node_id in active:
                raise ValueError("workflow graph contains a cycle")
            if node_id in visited:
                return
            active.add(node_id)
            for target in outgoing[node_id]:
                visit(target)
            active.remove(node_id)
            visited.add(node_id)

        visit(trigger.id)
        unreachable = set(nodes) - visited
        if unreachable:
            raise ValueError(f"unreachable workflow nodes: {', '.join(sorted(unreachable))}")
        for node in self.nodes:
            references = list(node.inputs.values())
            if isinstance(node, RetrieveNode):
                references.extend([node.query])
                if node.customer_id:
                    references.append(node.customer_id)
            elif isinstance(node, ToolNode):
                references.extend(node.arguments.values())
            elif isinstance(node, GenerateNode):
                references.append(node.prompt)
            for item in references:
                if item.reference and item.reference.node_id == "$item":
                    if not any(edge.target == node.id and edge.loop_over for edge in self.edges):
                        raise ValueError(f"node {node.id} uses $item outside a record loop")
                    continue
                if item.reference and item.reference.node_id not in nodes:
                    raise ValueError(
                        f"node {node.id} references missing output {item.reference.node_id}"
                    )
                if item.reference and item.reference.node_id not in _ancestors(node.id, incoming):
                    raise ValueError(
                        f"node {node.id} references non-predecessor output {item.reference.node_id}"
                    )
        for tool in (node for node in self.nodes if isinstance(node, ToolNode) and node.write):
            approvals = [
                node
                for node in self.nodes
                if isinstance(node, ApprovalNode) and node.tool_node_id == tool.id
            ]
            if len(approvals) != 1 or approvals[0].id not in _ancestors(tool.id, incoming):
                raise ValueError(f"write tool {tool.id} requires a preceding approval node")
        return self


_CONDITION = re.compile(
    r"[A-Za-z][A-Za-z0-9_-]*(?:\.[A-Za-z0-9_-]+)*"
    r"(?:\s*(?:==|!=|>=|<=|>|<)\s*(?:true|false|null|-?\d+(?:\.\d+)?|\"[^\"\\]*\"))?"
)


def _ancestors(node_id: str, incoming: dict[str, list[str]]) -> set[str]:
    found: set[str] = set()
    pending = list(incoming[node_id])
    while pending:
        current = pending.pop()
        if current not in found:
            found.add(current)
            pending.extend(incoming[current])
    return found


class WorkflowVersion(WorkflowModel):
    workflow_id: str
    version: int = Field(ge=1)
    definition: WorkflowDefinition
    published_at: datetime = Field(default_factory=utc_now)
    planner: dict[str, Any] | None = None
