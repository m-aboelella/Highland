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
from highland.workflows.planner import WorkflowPlanner, WorkflowPlanningError


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
    assert model.requests[0].tools == []
    assert model.requests[0].response_schema
    catalog = model.requests[0].messages[-1].content
    assert '"name": "pulse__post"' in catalog
    assert '"mode": "write"' in catalog
    assert '"input_schema": {"type": "object"}' in catalog
    assert "Use Tool nodes for fresh structured data and metrics" in (
        model.requests[0].messages[0].content
    )
    assert "Keep ordinary sequential edges unconditional" in (
        model.requests[0].messages[0].content
    )
    assert "stable customer IDs" in model.requests[0].messages[0].content
    assert "edges control order but do not pass data" in model.requests[0].messages[0].content
    assert "Problem to fix" in model.requests[1].messages[-1].content


@pytest.mark.asyncio
async def test_planner_explains_an_empty_model_response_without_pydantic_details() -> None:
    model = ScriptedChatModel(
        [response(None), response(None)], model="workflow-planner"
    )

    with pytest.raises(WorkflowPlanningError) as failure:
        await WorkflowPlanner(model, tools=[], policies={}).draft(
            "Every morning, show me the latest health metrics for northwind"
        )

    message = str(failure.value)
    assert message == (
        "Couldn't create the workflow because the model returned no workflow plan. "
        "Please try again."
    )
    assert "ProposedWorkflow" not in message
    assert "pydantic" not in message
    assert "NoneType" not in message


@pytest.mark.asyncio
async def test_planner_repairs_structured_data_disguised_as_indexed_retrieval() -> None:
    invalid = {
        "workflow": {
            "id": "wf_metrics",
            "name": "Daily metrics",
            "nodes": [
                {
                    "kind": "trigger",
                    "id": "start",
                    "name": "Every morning",
                    "trigger_type": "schedule",
                },
                {
                    "kind": "retrieve",
                    "id": "metrics",
                    "name": "Latest metrics",
                    "query": {"value": {"customer_id": "cus_northwind"}},
                },
            ],
            "edges": [{"source": "start", "target": "metrics"}],
        },
        "rationale": "Get current customer metrics.",
    }
    valid = {
        "workflow": {
            "id": "wf_metrics",
            "name": "Daily metrics",
            "nodes": [
                {
                    "kind": "trigger",
                    "id": "start",
                    "name": "Every morning",
                    "trigger_type": "schedule",
                },
                {
                    "kind": "tool",
                    "id": "metrics",
                    "name": "Latest metrics",
                    "tool": "observability__list_available_metrics",
                    "arguments": {"customer_id": {"value": "cus_northwind"}},
                    "write": False,
                },
            ],
            "edges": [{"source": "start", "target": "metrics"}],
        },
        "rationale": "Get current customer metrics from Beacon.",
    }
    model = ScriptedChatModel(
        [response(invalid), response(valid)], model="workflow-planner"
    )
    tool = ToolDefinition(
        name="observability__list_available_metrics",
        description="List current metrics",
        input_schema={
            "type": "object",
            "properties": {"customer_id": {"type": "string"}},
            "required": ["customer_id"],
        },
    )

    draft = await WorkflowPlanner(
        model,
        tools=[tool],
        policies={
            tool.name: ToolPolicy(mode=ToolMode.READ, customer_scoped=True)
        },
    ).draft("Every morning, show me the latest health metrics for northwind")

    assert draft.planner.repair_attempted is True
    assert draft.workflow.nodes[1].kind == "tool"
    assert "plain-text knowledge-search query" in model.requests[1].messages[-1].content


@pytest.mark.asyncio
async def test_planner_repairs_a_workflow_id_the_repository_cannot_save() -> None:
    invalid = plan()
    invalid["workflow"]["id"] = "daily_health"
    model = ScriptedChatModel(
        [response(invalid), response(plan())], model="workflow-planner"
    )
    tool = ToolDefinition(name="pulse__post", description="Post", input_schema={"type": "object"})

    draft = await WorkflowPlanner(
        model,
        tools=[tool],
        policies={
            tool.name: ToolPolicy(
                mode=ToolMode.WRITE,
                approval_required=True,
                idempotent_with_key=True,
            )
        },
    ).draft("Post a customer update")

    assert draft.workflow.id == "wf_post"
    assert draft.planner.repair_attempted is True
    assert "workflow.id" in model.requests[1].messages[-1].content


@pytest.mark.asyncio
async def test_planner_repairs_customer_slugs_and_unreferenced_tool_results() -> None:
    invalid = {
        "workflow": {
            "id": "wf_metrics",
            "name": "Daily metrics",
            "nodes": [
                {"kind": "trigger", "id": "start", "name": "Start"},
                {
                    "kind": "tool",
                    "id": "metrics",
                    "name": "Metrics",
                    "tool": "observability__list_available_metrics",
                    "arguments": {"customer_id": {"value": "northwind"}},
                    "write": False,
                },
                {
                    "kind": "generate",
                    "id": "report",
                    "name": "Report",
                    "prompt": {"value": "Summarize the metrics"},
                },
            ],
            "edges": [
                {"source": "start", "target": "metrics"},
                {"source": "metrics", "target": "report"},
            ],
        },
        "rationale": "Get current customer metrics.",
    }
    valid = invalid.copy()
    valid["workflow"] = {
        **invalid["workflow"],
        "nodes": [
            invalid["workflow"]["nodes"][0],
            {
                **invalid["workflow"]["nodes"][1],
                "arguments": {"customer_id": {"value": "cus_northwind"}},
            },
            {
                **invalid["workflow"]["nodes"][2],
                "inputs": {"metrics": {"reference": {"node_id": "metrics"}}},
            },
        ],
    }
    model = ScriptedChatModel([response(invalid), response(valid)], model="workflow-planner")
    tool = ToolDefinition(
        name="observability__list_available_metrics",
        description="List current metrics for a customer",
        input_schema={
            "type": "object",
            "properties": {"customer_id": {"type": "string"}},
            "required": ["customer_id"],
        },
    )

    draft = await WorkflowPlanner(
        model,
        tools=[tool],
        policies={tool.name: ToolPolicy(mode=ToolMode.READ, customer_scoped=True)},
    ).draft("Show Northwind metrics")

    assert draft.planner.repair_attempted is True
    assert draft.workflow.nodes[1].arguments["customer_id"].value == "cus_northwind"
    assert draft.workflow.nodes[2].inputs["metrics"].reference.node_id == "metrics"


@pytest.mark.asyncio
async def test_planner_wires_omitted_tool_outputs_without_another_model_call() -> None:
    planned = {
        "workflow": {
            "id": "wf_review",
            "name": "Review metrics",
            "nodes": [
                {"kind": "trigger", "id": "start", "name": "Start"},
                {
                    "kind": "tool",
                    "id": "get_customer",
                    "name": "Get customer",
                    "tool": "crm__get_customer",
                    "arguments": {"customer_id": {"value": "cus_northwind"}},
                    "write": False,
                },
                {
                    "kind": "tool",
                    "id": "get_metrics",
                    "name": "Get metrics",
                    "tool": "observability__list_available_metrics",
                    "arguments": {"customer_id": {"value": "cus_northwind"}},
                    "write": False,
                },
                {
                    "kind": "generate",
                    "id": "generate_review",
                    "name": "Review",
                    "prompt": {"value": "Review the customer metrics"},
                },
            ],
            "edges": [
                {"source": "start", "target": "get_customer"},
                {"source": "get_customer", "target": "get_metrics"},
                {"source": "get_metrics", "target": "generate_review"},
            ],
        },
        "rationale": "Review current metrics.",
    }
    model = ScriptedChatModel([response(planned)], model="workflow-planner")
    tools = [
        ToolDefinition(
            name="crm__get_customer",
            description="Get a customer",
            input_schema={"type": "object"},
        ),
        ToolDefinition(
            name="observability__list_available_metrics",
            description="List metrics",
            input_schema={"type": "object"},
        ),
    ]
    policies = {
        tool.name: ToolPolicy(mode=ToolMode.READ, customer_scoped=True)
        for tool in tools
    }

    draft = await WorkflowPlanner(model, tools=tools, policies=policies).draft(
        "Review Northwind metrics"
    )

    review = draft.workflow.nodes[-1]
    assert review.inputs["get_customer"].reference.node_id == "get_customer"
    assert review.inputs["get_metrics"].reference.node_id == "get_metrics"
    assert draft.planner.repair_attempted is False
    assert len(model.requests) == 1


@pytest.mark.asyncio
async def test_planner_repairs_an_incomplete_generate_response_schema() -> None:
    valid = {
        "workflow": {
            "id": "wf_report",
            "name": "Report",
            "nodes": [
                {"kind": "trigger", "id": "start", "name": "Start"},
                {
                    "kind": "generate",
                    "id": "report",
                    "name": "Report",
                    "prompt": {"value": "Write a narrative report"},
                },
            ],
            "edges": [{"source": "start", "target": "report"}],
        },
        "rationale": "Write a plain-text report.",
    }
    invalid = {
        **valid,
        "workflow": {
            **valid["workflow"],
            "nodes": [
                valid["workflow"]["nodes"][0],
                {
                    **valid["workflow"]["nodes"][1],
                    "response_schema": {
                        "type": "object",
                        "properties": {"details": {"type": "object"}},
                    },
                },
            ],
        },
    }
    model = ScriptedChatModel([response(invalid), response(valid)], model="workflow-planner")

    draft = await WorkflowPlanner(model, tools=[], policies={}).draft("Write a report")

    assert draft.workflow.nodes[1].response_schema is None
    assert draft.planner.repair_attempted is True
    assert "must define non-empty properties" in model.requests[1].messages[-1].content
