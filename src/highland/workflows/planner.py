from __future__ import annotations

import json

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from highland.models.contracts import (
    ChatModel,
    ChatRequest,
    Message,
    MessageRole,
    ModelCapabilities,
    ToolDefinition,
    Usage,
)
from highland.runtime.policy import ToolMode, ToolPolicy

from .schema import (
    GenerateNode,
    NodeInput,
    OutputReference,
    RetrieveNode,
    ToolNode,
    WorkflowDefinition,
)


class PlannerModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ProposedWorkflow(PlannerModel):
    workflow: WorkflowDefinition
    rationale: str = Field(min_length=1)


class PlannerRecord(PlannerModel):
    model: str
    provider: str
    request_id: str | None = None
    usage: Usage
    rationale: str
    repair_attempted: bool = False


class WorkflowDraft(PlannerModel):
    workflow: WorkflowDefinition
    planner: PlannerRecord
    requires_human_review: bool = True


class WorkflowPlanningError(ValueError):
    """A bounded planning attempt could not produce a valid, policy-safe workflow."""


class WorkflowPlanner:
    def __init__(
        self,
        chat_model: ChatModel,
        *,
        tools: list[ToolDefinition],
        policies: dict[str, ToolPolicy],
        max_repairs: int = 1,
    ) -> None:
        self.chat_model = chat_model
        self.tools = tools
        self.policies = policies
        self.max_repairs = max_repairs

    async def draft(self, goal: str) -> WorkflowDraft:
        available = {tool.name: tool for tool in self.tools}
        tool_catalog = [
            {
                "name": tool.name,
                "description": tool.description,
                "input_schema": tool.input_schema,
                "policy": {
                    "mode": self.policies[tool.name].mode.value,
                    "approval_required": self.policies[tool.name].approval_required,
                    "customer_scoped": self.policies[tool.name].customer_scoped,
                },
            }
            for tool in self.tools
            if tool.name in self.policies
        ]
        messages = [
            Message(
                role=MessageRole.SYSTEM,
                content=(
                    "Convert the user's goal into the supplied Highland workflow JSON schema. "
                    "Return one JSON workflow plan; do not call tools. Use only tools in the "
                    "supplied catalog and copy their names and argument shapes exactly. Write "
                    "tools must set write=true and have a "
                    "preceding Approval node linked by tool_node_id. Produce a plan for a human "
                    "to review; never claim to publish, schedule, or execute it. Use Tool nodes "
                    "for fresh structured data and metrics. Use Retrieve nodes only for indexed "
                    "knowledge search, with a plain-text query. Keep ordinary sequential edges "
                    "unconditional. Only add a condition when the goal explicitly requires a "
                    "branch, and use a machine expression such as "
                    "health.classification == \"at_risk\", never prose. Customer-scoped tools "
                    "require stable customer IDs. Treat examples in tool descriptions as "
                    "authoritative and never replace an ID such as cus_northwind with a display "
                    "slug. A Generate node that summarizes tool results must reference every "
                    "result in its inputs; edges control order but do not pass data. Omit "
                    "response_schema for narrative reports. When structured output is necessary, "
                    "every object in its JSON schema must define explicit non-empty properties."
                ),
            ),
            Message(
                role=MessageRole.USER,
                content=(
                    f"Goal:\n{goal}\n\n"
                    "Available tool catalog (planning context only; do not call these tools):\n"
                    f"{json.dumps(tool_catalog, sort_keys=True)}"
                ),
            ),
        ]
        errors: list[str] = []
        response = None
        proposed = None
        for attempt in range(self.max_repairs + 1):
            response = await self.chat_model.chat(
                ChatRequest(
                    messages=messages,
                    response_schema=ProposedWorkflow.model_json_schema(),
                    required_capabilities=ModelCapabilities(structured_output=True),
                    logical_call_id=f"workflow-plan-{attempt + 1}",
                )
            )
            try:
                if response.structured_output is None:
                    raise ValueError("returned no workflow plan")
                if not isinstance(response.structured_output, dict):
                    raise TypeError("returned an unreadable workflow plan")
                proposed = ProposedWorkflow.model_validate(response.structured_output)
                proposed = proposed.model_copy(
                    update={"workflow": _wire_generate_inputs(proposed.workflow)}
                )
                self._validate_tools(proposed.workflow, available)
                break
            except (ValidationError, TypeError, ValueError) as error:
                errors.append(_planning_error_detail(error))
                if attempt >= self.max_repairs:
                    raise WorkflowPlanningError(
                        f"Couldn't create the workflow because the model {errors[-1]}. "
                        "Please try again."
                    ) from error
                messages.extend(
                    [
                        Message(
                            role=MessageRole.ASSISTANT,
                            content=json.dumps(response.structured_output, default=str),
                        ),
                        Message(
                            role=MessageRole.USER,
                            content=(
                                "Repair the workflow and return the complete structured plan. "
                                f"Problem to fix: {errors[-1]}. Remove edge conditions for "
                                "ordinary sequencing; conditions may only compare a prior node "
                                "field with a JSON literal."
                            ),
                        ),
                    ]
                )
        assert response is not None and proposed is not None
        return WorkflowDraft(
            workflow=proposed.workflow,
            planner=PlannerRecord(
                model=response.metadata.model,
                provider=response.metadata.provider,
                request_id=response.metadata.request_id,
                usage=response.usage,
                rationale=proposed.rationale,
                repair_attempted=bool(errors),
            ),
        )

    def _validate_tools(
        self, workflow: WorkflowDefinition, available: dict[str, ToolDefinition]
    ) -> None:
        incoming: dict[str, list[str]] = {node.id: [] for node in workflow.nodes}
        nodes = {node.id: node for node in workflow.nodes}
        for edge in workflow.edges:
            incoming[edge.target].append(edge.source)
        for node in workflow.nodes:
            if (
                isinstance(node, RetrieveNode)
                and node.query.value is not None
                and not isinstance(node.query.value, str)
            ):
                raise TypeError(
                    f"retrieve node {node.id} must use a plain-text knowledge-search query"
                )
            if not isinstance(node, ToolNode):
                continue
            if node.tool not in available:
                raise ValueError(f"workflow uses unavailable tool {node.tool}")
            policy = self.policies.get(node.tool)
            if policy is None:
                raise ValueError(f"workflow tool has no policy {node.tool}")
            customer_id = node.arguments.get("customer_id")
            if (
                policy.customer_scoped
                and customer_id is not None
                and isinstance(customer_id.value, str)
                and not customer_id.value.startswith("cus_")
            ):
                raise ValueError(
                    f"workflow tool {node.tool} must use a stable customer ID beginning with cus_"
                )
            expected_write = policy.mode is ToolMode.WRITE
            if node.write != expected_write:
                raise ValueError(f"workflow tool {node.tool} has incorrect write classification")
        for node in workflow.nodes:
            if not isinstance(node, GenerateNode):
                continue
            tool_ancestors = {
                ancestor
                for ancestor in _planner_ancestors(node.id, incoming)
                if isinstance(nodes[ancestor], ToolNode)
            }
            referenced = {
                item.reference.node_id
                for item in node.inputs.values()
                if item.reference is not None
            }
            unused = sorted(tool_ancestors - referenced)
            if unused:
                raise ValueError(
                    f"generate node {node.id} must reference tool outputs in its inputs: "
                    + ", ".join(unused)
                )
            if node.response_schema is not None:
                _validate_generate_schema(node.response_schema, path="response_schema")


def _planning_error_detail(error: ValidationError | TypeError | ValueError) -> str:
    if not isinstance(error, ValidationError):
        return str(error).rstrip(".")
    details: list[str] = []
    for issue in error.errors(include_url=False, include_context=False):
        location = ".".join(str(part) for part in issue["loc"])
        message = str(issue["msg"]).removeprefix("Value error, ")
        details.append(f"{location}: {message}" if location else message)
    return "generated an invalid plan: " + "; ".join(details)


def _planner_ancestors(node_id: str, incoming: dict[str, list[str]]) -> set[str]:
    found: set[str] = set()
    pending = list(incoming[node_id])
    while pending:
        current = pending.pop()
        if current not in found:
            found.add(current)
            pending.extend(incoming[current])
    return found


def _wire_generate_inputs(workflow: WorkflowDefinition) -> WorkflowDefinition:
    """Add mechanical data-flow references the model omitted from Generate nodes."""
    incoming: dict[str, list[str]] = {node.id: [] for node in workflow.nodes}
    nodes = {node.id: node for node in workflow.nodes}
    for edge in workflow.edges:
        incoming[edge.target].append(edge.source)
    updated = []
    for node in workflow.nodes:
        if not isinstance(node, GenerateNode):
            updated.append(node)
            continue
        referenced = {
            item.reference.node_id
            for item in node.inputs.values()
            if item.reference is not None
        }
        missing = sorted(
            ancestor
            for ancestor in _planner_ancestors(node.id, incoming)
            if isinstance(nodes[ancestor], ToolNode) and ancestor not in referenced
        )
        inputs = dict(node.inputs)
        for tool_node_id in missing:
            input_name = tool_node_id
            suffix = 2
            while input_name in inputs:
                input_name = f"{tool_node_id}_{suffix}"
                suffix += 1
            inputs[input_name] = NodeInput(
                reference=OutputReference(node_id=tool_node_id)
            )
        updated.append(node.model_copy(update={"inputs": inputs}))
    return workflow.model_copy(update={"nodes": updated})


def _validate_generate_schema(value: object, *, path: str) -> None:
    if isinstance(value, dict):
        if value.get("type") == "object":
            properties = value.get("properties")
            if not isinstance(properties, dict) or not properties:
                raise ValueError(f"{path} object must define non-empty properties")
        for key, item in value.items():
            _validate_generate_schema(item, path=f"{path}.{key}")
    elif isinstance(value, list):
        for index, item in enumerate(value):
            _validate_generate_schema(item, path=f"{path}.{index}")
