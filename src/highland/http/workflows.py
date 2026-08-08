"""Workflow planning, publication, execution, and scheduling HTTP routes."""

from uuid import uuid4

from fastapi import APIRouter, HTTPException, status

from highland.models.contracts import ModelError
from highland.runtime.mcp import MCPGateway
from highland.runtime.policy import ToolRegistry
from highland.services import ApplicationServices
from highland.workflows import WorkflowDefinition, WorkflowPlanner, WorkflowPlanningError

from .models import (
    DraftWorkflowRequest,
    PublishWorkflowRequest,
    RunWorkflowRequest,
    SaveWorkflowRequest,
    ScheduleWorkflowRequest,
)


def create_workflow_router(services: ApplicationServices) -> APIRouter:
    router = APIRouter(tags=["workflows"])
    workflows = services.workflows

    @router.post("/workflows/draft")
    async def draft_workflow(request: DraftWorkflowRequest) -> dict[str, object]:
        configured = services.settings
        async with MCPGateway(
            configured.connector_commands,
            startup_timeout_seconds=configured.connector_timeout_seconds,
            request_timeout_seconds=configured.connector_timeout_seconds,
        ) as gateway:
            registry = ToolRegistry.from_file(gateway, configured.tool_policy_config)
            planner = WorkflowPlanner(
                services.provider.chat,
                tools=gateway.model_tools(),
                policies=registry.model_tool_policies(),
            )
            try:
                draft = await planner.draft(request.goal)
            except WorkflowPlanningError as error:
                raise HTTPException(status_code=422, detail=str(error)) from error
            except ModelError as error:
                raise HTTPException(
                    status_code=status.HTTP_502_BAD_GATEWAY,
                    detail=f"Couldn't create the workflow because the model request failed: {error}",
                ) from error
        return draft.model_dump(mode="json")

    @router.post("/workflows", status_code=status.HTTP_201_CREATED)
    async def save_workflow(request: SaveWorkflowRequest) -> dict[str, object]:
        saved = workflows.save_draft(request.workflow)
        payload = saved.model_dump(mode="json")
        if request.planner:
            payload["planner"] = request.planner.model_dump(mode="json")
        return payload

    @router.get("/workflows")
    async def list_workflows() -> list[dict[str, object]]:
        return [workflow.model_dump(mode="json") for workflow in workflows.list()]

    @router.get("/workflows/{workflow_id}")
    async def get_workflow(workflow_id: str) -> dict[str, object]:
        try:
            draft = workflows.get_draft(workflow_id)
        except FileNotFoundError:
            raise HTTPException(status_code=404, detail="Workflow not found") from None
        return {
            "draft": draft.model_dump(mode="json"),
            "versions": [
                version.model_dump(mode="json") for version in workflows.list_versions(workflow_id)
            ],
        }

    @router.post("/workflows/{workflow_id}/publish")
    async def publish_workflow(
        workflow_id: str, request: PublishWorkflowRequest | None = None
    ) -> dict[str, object]:
        if request is not None and request.workflow is not None:
            if request.workflow.id != workflow_id:
                raise HTTPException(
                    status_code=422,
                    detail="The workflow ID does not match the draft being published",
                )
            workflows.save_draft(request.workflow)
        try:
            return workflows.publish(workflow_id).model_dump(mode="json")
        except FileNotFoundError:
            raise HTTPException(status_code=404, detail="Workflow draft not found") from None

    @router.post("/workflows/{workflow_id}/runs")
    async def run_workflow(workflow_id: str, request: RunWorkflowRequest) -> dict[str, object]:
        definition, version = _workflow_for_run(services, workflow_id, request)
        run_id = f"wrun_{uuid4().hex}"
        run = await services.execute_workflow(
            definition,
            run_id=run_id,
            workflow_version=version,
            trigger=request.trigger,
            test=request.test,
        )
        return {**run.model_dump(mode="json"), "trace_url": f"/runs/{run_id}/trace"}

    @router.get("/workflow-runs")
    async def list_workflow_runs() -> list[dict[str, object]]:
        return [run.model_dump(mode="json") for run in services.workflow_runs.list()]

    @router.post("/workflows/{workflow_id}/schedules", status_code=status.HTTP_201_CREATED)
    async def schedule_workflow(
        workflow_id: str, request: ScheduleWorkflowRequest
    ) -> dict[str, object]:
        try:
            published = workflows.get_version(workflow_id, request.version)
        except FileNotFoundError:
            raise HTTPException(
                status_code=422, detail="Only a published workflow version can be scheduled"
            ) from None
        return services.workflow_schedules.create(
            published, interval_seconds=request.interval_seconds
        ).model_dump(mode="json")

    return router


def _workflow_for_run(
    services: ApplicationServices,
    workflow_id: str,
    request: RunWorkflowRequest,
) -> tuple[WorkflowDefinition, int]:
    if request.test:
        if request.workflow is not None:
            if request.workflow.id != workflow_id:
                raise HTTPException(
                    status_code=422,
                    detail="The test workflow ID does not match the requested workflow",
                )
            definition = services.workflows.save_draft(request.workflow)
        else:
            try:
                definition = services.workflows.get_draft(workflow_id)
            except FileNotFoundError:
                raise HTTPException(
                    status_code=404,
                    detail="Workflow draft not found. Include or save the draft before testing.",
                ) from None
        return definition, request.version or 0
    if request.workflow is not None:
        raise HTTPException(status_code=422, detail="Inline drafts can only be used for test runs")
    if request.version is None:
        raise HTTPException(status_code=422, detail="Published version is required")
    try:
        published = services.workflows.get_version(workflow_id, request.version)
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail="Workflow version not found") from None
    return published.definition, published.version
