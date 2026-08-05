from __future__ import annotations

import json
import os
import re
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
from highland.runtime.approvals import ApprovalService, ApprovalStatus, ApprovalStore
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
        approvals: ApprovalStore | None = None,
    ) -> None:
        self.model = model
        self.tools = tools
        self.repository = repository
        self.retriever = retriever
        self.budgets = budgets or WorkflowBudgets()
        self.approvals = approvals

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
                incoming = [edge for edge in definition.edges if edge.target == node.id]
                active = [
                    edge
                    for edge in incoming
                    if run.nodes[edge.source].status is NodeRunStatus.COMPLETED
                    and (edge.condition is None or evaluate_condition(edge.condition, run))
                ]
                if incoming and not active:
                    record.status = NodeRunStatus.SKIPPED
                    record.completed_at = datetime.now(UTC)
                    self._save(run)
                    continue
                if isinstance(node, ApprovalNode) and node.id not in approved_nodes:
                    if self.approvals is None:
                        record.status = NodeRunStatus.WAITING_APPROVAL
                        record.inputs = {"tool_node_id": node.tool_node_id, "reason": node.reason}
                        run.status = WorkflowRunStatus.PAUSED
                        self._save(run)
                        return run
                    approval_id = (
                        record.output.get("approval_id")
                        if isinstance(record.output, dict)
                        else None
                    )
                    if approval_id is None:
                        tool_node = next(
                            item
                            for item in definition.nodes
                            if isinstance(item, ToolNode) and item.id == node.tool_node_id
                        )
                        arguments = _node_inputs(tool_node, run)
                        checked = self.tools.validate(
                            tool_node.tool,
                            arguments,
                            run_id=run.id,
                            logical_step_id=tool_node.id,
                            scope=scope,
                        )
                        approval = self.approvals.create(
                            run_id=run.id,
                            tool_call_id=tool_node.id,
                            call=checked,
                            reason=node.reason,
                        )
                        approval_id = approval.id
                        record.output = {
                            "approval_id": approval.id,
                            "tool_node_id": node.tool_node_id,
                        }
                    approval = self.approvals.get(approval_id)
                    if approval.status in (ApprovalStatus.PENDING, ApprovalStatus.EXPIRED):
                        record.status = NodeRunStatus.WAITING_APPROVAL
                        record.inputs = {"tool_node_id": node.tool_node_id, "reason": node.reason}
                        run.status = WorkflowRunStatus.PAUSED
                        self._save(run)
                        return run
                    if approval.status is ApprovalStatus.REJECTED:
                        raise RuntimeError(
                            f"workflow approval rejected: {approval.decision_reason or 'no reason'}"
                        )
                record.status = NodeRunStatus.RUNNING
                record.attempts += 1
                record.started_at = datetime.now(UTC)
                node_started = time.monotonic()
                try:
                    loop_edge = next((edge for edge in active if edge.loop_over), None)
                    if loop_edge:
                        records = resolve_reference(loop_edge.loop_over, run)
                        if not isinstance(records, list):
                            raise TypeError("workflow loop input must be a list")
                        if len(records) > loop_edge.max_iterations:
                            raise RuntimeError(
                                f"workflow loop exceeds {loop_edge.max_iterations} iterations"
                            )
                        iterations = []
                        total_usage = Usage(input_tokens=0, output_tokens=0)
                        for index, item in enumerate(records):
                            inputs = _node_inputs(node, run, loop_item=item)
                            isolated_scope = _loop_scope(scope, item)
                            output, usage = await self._execute_node(
                                node, inputs=inputs, run=run, scope=isolated_scope
                            )
                            iterations.append(
                                {"index": index, "record": item, "output": output}
                            )
                            total_usage = _add_usage(total_usage, usage)
                        record.inputs = {"iterations": [item["record"] for item in iterations]}
                        output, usage = iterations, total_usage
                    else:
                        inputs = _node_inputs(node, run)
                        record.inputs = inputs
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
            existing = run.nodes[node.id].output
            return (
                {**existing, "approved": True}
                if isinstance(existing, dict)
                else {"approved": True},
                Usage(),
            )
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
            prompt = str(inputs["prompt"])
            context = {name: value for name, value in inputs.items() if name != "prompt"}
            if context:
                prompt += "\n\nWorkflow inputs:\n" + json.dumps(
                    context,
                    sort_keys=True,
                    default=str,
                )
            response = await self.model.chat(
                ChatRequest(
                    messages=[
                        Message(
                            role=MessageRole.SYSTEM,
                            content="Execute this reviewed workflow generation step faithfully.",
                        ),
                        Message(role=MessageRole.USER, content=prompt),
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
            if node.write and self.approvals:
                approval_id = next(
                    (
                        item.output["approval_id"]
                        for item in run.nodes.values()
                        if isinstance(item.output, dict)
                        and item.output.get("tool_node_id") == node.id
                        and "approval_id" in item.output
                    ),
                    None,
                )
                if approval_id is None:
                    raise RuntimeError(f"write node {node.id} has no durable approval")
                result = await ApprovalService(self.approvals, self.tools).resume(approval_id)
            else:
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


def resolve_reference(
    reference: OutputReference, run: WorkflowRun, *, loop_item: Any = None
) -> Any:
    if reference.node_id == "$item":
        value = loop_item
    else:
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


def resolve_input(value: NodeInput, run: WorkflowRun, *, loop_item: Any = None) -> Any:
    return (
        resolve_reference(value.reference, run, loop_item=loop_item)
        if value.reference
        else value.value
    )


def _node_inputs(node: Any, run: WorkflowRun, *, loop_item: Any = None) -> dict[str, Any]:
    resolved = {
        name: resolve_input(value, run, loop_item=loop_item)
        for name, value in node.inputs.items()
    }
    if isinstance(node, RetrieveNode):
        resolved["query"] = resolve_input(node.query, run, loop_item=loop_item)
        if node.customer_id:
            resolved["customer_id"] = resolve_input(
                node.customer_id, run, loop_item=loop_item
            )
    elif isinstance(node, ToolNode):
        resolved.update(
            {
                name: resolve_input(value, run, loop_item=loop_item)
                for name, value in node.arguments.items()
            }
        )
    elif isinstance(node, GenerateNode):
        resolved["prompt"] = resolve_input(node.prompt, run, loop_item=loop_item)
    return resolved


_EXPRESSION = re.compile(
    r"(?P<reference>[A-Za-z][A-Za-z0-9_-]*(?:\.[A-Za-z0-9_-]+)*)"
    r"(?:\s*(?P<operator>==|!=|>=|<=|>|<)\s*(?P<value>.+))?"
)


def evaluate_condition(expression: str, run: WorkflowRun) -> bool:
    match = _EXPRESSION.fullmatch(expression.strip())
    if not match:
        raise ValueError("invalid workflow branch expression")
    reference, _, path = match.group("reference").partition(".")
    value = resolve_reference(OutputReference(node_id=reference, path=path), run)
    operator = match.group("operator")
    if operator is None:
        return bool(value)
    try:
        expected = json.loads(match.group("value"))
    except json.JSONDecodeError as error:
        raise ValueError("branch comparison value must be JSON") from error
    operations = {
        "==": lambda: value == expected,
        "!=": lambda: value != expected,
        ">": lambda: value > expected,
        ">=": lambda: value >= expected,
        "<": lambda: value < expected,
        "<=": lambda: value <= expected,
    }
    try:
        return bool(operations[operator]())
    except TypeError as error:
        raise ValueError("branch values are not comparable") from error


def _loop_scope(scope: RunScope, item: Any) -> RunScope:
    if not isinstance(item, dict) or not isinstance(item.get("customer_id"), str):
        return scope
    customer_id = item["customer_id"]
    if scope.allowed_customers and customer_id not in scope.allowed_customers:
        raise ValueError(f"loop customer {customer_id!r} is outside this run's scope")
    return RunScope(
        allowed_customers=frozenset({customer_id}),
        allowed_visibilities=scope.allowed_visibilities,
        allow_writes=scope.allow_writes,
    )


def _add_usage(left: Usage, right: Usage) -> Usage:
    def add(name: str) -> int | float | None:
        first = getattr(left, name)
        second = getattr(right, name)
        return None if first is None and second is None else (first or 0) + (second or 0)

    return Usage(
        input_tokens=add("input_tokens"),
        output_tokens=add("output_tokens"),
        billed_input_tokens=add("billed_input_tokens"),
        billed_output_tokens=add("billed_output_tokens"),
        search_units=add("search_units"),
    )


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
