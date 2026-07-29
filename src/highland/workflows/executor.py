from __future__ import annotations

import os
import time
from datetime import UTC, datetime
from enum import StrEnum
from pathlib import Path
from typing import Any, Protocol

from pydantic import BaseModel, ConfigDict, Field

from highland.models.contracts import (
    ChatModel,
    ChatRequest,
    Message,
    MessageRole,
    ModelCapabilities,
    Usage,
)
from highland.retrieval.hybrid import RetrievalFilters
from highland.runtime.policy import RunScope, ToolRegistry

from .schema import (
    ApprovalNode,
    GenerateNode,
    NodeInput,
    OutputReference,
    RetrieveNode,
    ToolNode,
    TriggerNode,
    WorkflowDefinition,
)


class Retriever(Protocol):
    async def search(self, query: str, *, filters: RetrievalFilters) -> Any: ...


class ExecutionModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class WorkflowRunStatus(StrEnum):
    RUNNING = "running"
    PAUSED = "paused"
    COMPLETED = "completed"
    FAILED = "failed"


class NodeRunStatus(StrEnum):
    PENDING = "pending"
    RUNNING = "running"
    WAITING_APPROVAL = "waiting_approval"
    COMPLETED = "completed"
    FAILED = "failed"
    SKIPPED = "skipped"


class WorkflowBudgets(ExecutionModel):
    max_model_calls: int = Field(default=10, ge=0)
    max_tool_calls: int = Field(default=25, ge=0)
    max_wall_seconds: float = Field(default=300, gt=0)


class NodeRun(ExecutionModel):
    node_id: str
    status: NodeRunStatus = NodeRunStatus.PENDING
    attempts: int = 0
    started_at: datetime | None = None
    completed_at: datetime | None = None
    duration_ms: float | None = None
    inputs: dict[str, Any] = Field(default_factory=dict)
    output: Any = None
    usage: Usage = Field(default_factory=Usage)
    error: str | None = None


class WorkflowRun(ExecutionModel):
    schema_version: int = 1
    id: str
    workflow_id: str
    workflow_version: int
    status: WorkflowRunStatus = WorkflowRunStatus.RUNNING
    nodes: dict[str, NodeRun] = Field(default_factory=dict)
    model_calls: int = 0
    tool_calls: int = 0
    started_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    error: str | None = None
    trigger: dict[str, Any] = Field(default_factory=dict)


class WorkflowRunRepository:
    def __init__(self, directory: Path) -> None:
        self.directory = directory

    def save(self, run: WorkflowRun) -> None:
        self.directory.mkdir(parents=True, exist_ok=True)
        target = self.directory / f"{run.id}.json"
        temporary = target.with_suffix(".tmp")
        temporary.write_text(run.model_dump_json(indent=2), encoding="utf-8")
        os.replace(temporary, target)

    def get(self, run_id: str) -> WorkflowRun:
        return WorkflowRun.model_validate_json(
            (self.directory / f"{run_id}.json").read_text("utf-8")
        )

    def list(self) -> list[WorkflowRun]:
        if not self.directory.exists():
            return []
        return sorted(
            (
                WorkflowRun.model_validate_json(path.read_text("utf-8"))
                for path in self.directory.glob("*.json")
            ),
            key=lambda run: run.started_at,
            reverse=True,
        )


class WorkflowExecutor:
    def __init__(
        self,
        *,
        model: ChatModel,
        tools: ToolRegistry | Any,
        repository: WorkflowRunRepository,
        retriever: Retriever | None = None,
        budgets: WorkflowBudgets | None = None,
    ) -> None:
        self.model = model
        self.tools = tools
        self.repository = repository
        self.retriever = retriever
        self.budgets = budgets or WorkflowBudgets()

    async def run(
        self,
        definition: WorkflowDefinition,
        *,
        run_id: str,
        workflow_version: int = 0,
        trigger: dict[str, Any] | None = None,
        scope: RunScope | None = None,
        approved_nodes: set[str] | None = None,
    ) -> WorkflowRun:
        scope = scope or RunScope()
        approved_nodes = approved_nodes or set()
        try:
            run = self.repository.get(run_id)
            if run.workflow_id != definition.id or run.workflow_version != workflow_version:
                raise ValueError("run checkpoint does not match workflow version")
            if run.status is WorkflowRunStatus.COMPLETED:
                return run
            run.status = WorkflowRunStatus.RUNNING
            run.error = None
        except FileNotFoundError:
            run = WorkflowRun(
                id=run_id,
                workflow_id=definition.id,
                workflow_version=workflow_version,
                trigger=trigger or {},
                nodes={node.id: NodeRun(node_id=node.id) for node in definition.nodes},
            )
            self.repository.save(run)
        started = time.monotonic()
        try:
            for node in _ordered_nodes(definition):
                record = run.nodes[node.id]
                if record.status is NodeRunStatus.COMPLETED:
                    continue
                self._check_time(started)
                if isinstance(node, ApprovalNode) and node.id not in approved_nodes:
                    record.status = NodeRunStatus.WAITING_APPROVAL
                    record.inputs = {"tool_node_id": node.tool_node_id, "reason": node.reason}
                    run.status = WorkflowRunStatus.PAUSED
                    self._save(run)
                    return run
                record.status = NodeRunStatus.RUNNING
                record.attempts += 1
                record.started_at = datetime.now(UTC)
                node_started = time.monotonic()
                inputs = _node_inputs(node, run)
                record.inputs = inputs
                try:
                    output, usage = await self._execute_node(
                        node, inputs=inputs, run=run, scope=scope
                    )
                except Exception as error:  # noqa: BLE001 - persist bounded workflow failures
                    record.status = NodeRunStatus.FAILED
                    record.error = f"{type(error).__name__}: {error}"[:2000]
                    record.completed_at = datetime.now(UTC)
                    record.duration_ms = (time.monotonic() - node_started) * 1000
                    run.status = WorkflowRunStatus.FAILED
                    run.error = record.error
                    self._save(run)
                    return run
                record.output = output
                record.usage = usage
                record.status = NodeRunStatus.COMPLETED
                record.completed_at = datetime.now(UTC)
                record.duration_ms = (time.monotonic() - node_started) * 1000
                self._save(run)
        except Exception as error:  # noqa: BLE001 - persist bounded workflow failures
            run.status = WorkflowRunStatus.FAILED
            run.error = f"{type(error).__name__}: {error}"[:2000]
            self._save(run)
            return run
        run.status = WorkflowRunStatus.COMPLETED
        self._save(run)
        return run

    async def _execute_node(
        self,
        node: Any,
        *,
        inputs: dict[str, Any],
        run: WorkflowRun,
        scope: RunScope,
    ) -> tuple[Any, Usage]:
        if isinstance(node, TriggerNode):
            return run.trigger, Usage()
        if isinstance(node, ApprovalNode):
            return {"approved": True}, Usage()
        if isinstance(node, RetrieveNode):
            if self.retriever is None:
                raise RuntimeError("workflow retrieval is not configured")
            response = await self.retriever.search(
                str(inputs["query"]),
                filters=RetrievalFilters(customer_id=inputs.get("customer_id")),
            )
            output = response.model_dump(mode="json") if hasattr(response, "model_dump") else response
            return output, getattr(response, "rerank_usage", Usage())
        if isinstance(node, GenerateNode):
            if run.model_calls >= self.budgets.max_model_calls:
                raise RuntimeError("workflow model-call budget exceeded")
            response = await self.model.chat(
                ChatRequest(
                    messages=[
                        Message(
                            role=MessageRole.SYSTEM,
                            content="Execute this reviewed workflow generation step faithfully.",
                        ),
                        Message(role=MessageRole.USER, content=str(inputs["prompt"])),
                    ],
                    response_schema=node.response_schema,
                    required_capabilities=ModelCapabilities(
                        structured_output=node.response_schema is not None
                    ),
                    logical_call_id=f"{run.id}:{node.id}:{run.nodes[node.id].attempts}",
                )
            )
            run.model_calls += 1
            return (
                response.structured_output
                if node.response_schema is not None
                else response.message.content,
                response.usage,
            )
        if isinstance(node, ToolNode):
            if run.tool_calls >= self.budgets.max_tool_calls:
                raise RuntimeError("workflow tool-call budget exceeded")
            checked = self.tools.validate(
                node.tool,
                inputs,
                run_id=run.id,
                logical_step_id=node.id,
                scope=scope,
            )
            result = (
                await self.tools.execute_approved(checked)
                if node.write
                else await self.tools.execute(checked)
            )
            run.tool_calls += 1
            if result.is_error:
                raise RuntimeError(result.content)
            return (
                result.structured_content
                if result.structured_content is not None
                else result.content,
                Usage(),
            )
        raise TypeError(f"unsupported workflow node {type(node).__name__}")

    def _check_time(self, started: float) -> None:
        if time.monotonic() - started >= self.budgets.max_wall_seconds:
            raise RuntimeError("workflow wall-time budget exceeded")

    def _save(self, run: WorkflowRun) -> None:
        run.updated_at = datetime.now(UTC)
        self.repository.save(run)


def resolve_reference(reference: OutputReference, run: WorkflowRun) -> Any:
    if reference.node_id not in run.nodes:
        raise ValueError(f"unknown node output {reference.node_id}")
    value = run.nodes[reference.node_id].output
    if reference.path:
        for part in reference.path.split("."):
            if isinstance(value, list):
                value = value[int(part)]
            elif isinstance(value, dict):
                value = value[part]
            else:
                raise TypeError(f"cannot resolve {reference.path} from {reference.node_id}")
    return value


def resolve_input(value: NodeInput, run: WorkflowRun) -> Any:
    return resolve_reference(value.reference, run) if value.reference else value.value


def _node_inputs(node: Any, run: WorkflowRun) -> dict[str, Any]:
    resolved = {name: resolve_input(value, run) for name, value in node.inputs.items()}
    if isinstance(node, RetrieveNode):
        resolved["query"] = resolve_input(node.query, run)
        if node.customer_id:
            resolved["customer_id"] = resolve_input(node.customer_id, run)
    elif isinstance(node, ToolNode):
        resolved.update(
            {name: resolve_input(value, run) for name, value in node.arguments.items()}
        )
    elif isinstance(node, GenerateNode):
        resolved["prompt"] = resolve_input(node.prompt, run)
    return resolved


def _ordered_nodes(definition: WorkflowDefinition) -> list[Any]:
    nodes = {node.id: node for node in definition.nodes}
    incoming = {node_id: 0 for node_id in nodes}
    outgoing: dict[str, list[str]] = {node_id: [] for node_id in nodes}
    for edge in definition.edges:
        incoming[edge.target] += 1
        outgoing[edge.source].append(edge.target)
    ready = sorted(node_id for node_id, count in incoming.items() if count == 0)
    ordered = []
    while ready:
        node_id = ready.pop(0)
        ordered.append(nodes[node_id])
        for target in sorted(outgoing[node_id]):
            incoming[target] -= 1
            if incoming[target] == 0:
                ready.append(target)
                ready.sort()
    return ordered
