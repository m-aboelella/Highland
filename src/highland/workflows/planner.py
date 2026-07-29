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

from .schema import ToolNode, WorkflowDefinition


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
        policy_payload = {
            name: {
                "mode": policy.mode.value,
                "approval_required": policy.approval_required,
                "customer_scoped": policy.customer_scoped,
            }
            for name, policy in self.policies.items()
        }
        messages = [
            Message(
                role=MessageRole.SYSTEM,
                content=(
                    "Convert the user's goal into the supplied Highland workflow JSON schema. "
                    "Use only the supplied tools. Write tools must set write=true and have a "
                    "preceding Approval node linked by tool_node_id. Produce a plan for a human "
                    "to review; never claim to publish, schedule, or execute it."
                ),
            ),
            Message(
                role=MessageRole.USER,
                content=(
                    f"Goal:\n{goal}\n\n"
                    f"Available tool policies:\n{json.dumps(policy_payload, sort_keys=True)}"
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
                    tools=self.tools,
                    response_schema=ProposedWorkflow.model_json_schema(),
                    required_capabilities=ModelCapabilities(structured_output=True),
                    logical_call_id=f"workflow-plan-{attempt + 1}",
                )
            )
            try:
                proposed = ProposedWorkflow.model_validate(response.structured_output)
                self._validate_tools(proposed.workflow, available)
                break
            except (ValidationError, ValueError) as error:
                errors.append(str(error))
                if attempt >= self.max_repairs:
                    raise WorkflowPlanningError(
                        "Model workflow failed validation: " + "; ".join(errors)
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
                                f"Validation error: {error}"
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
        for node in workflow.nodes:
            if not isinstance(node, ToolNode):
                continue
            if node.tool not in available:
                raise ValueError(f"workflow uses unavailable tool {node.tool}")
            policy = self.policies.get(node.tool)
            if policy is None:
                raise ValueError(f"workflow tool has no policy {node.tool}")
            expected_write = policy.mode is ToolMode.WRITE
            if node.write != expected_write:
                raise ValueError(f"workflow tool {node.tool} has incorrect write classification")
