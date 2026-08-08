from __future__ import annotations

import asyncio
from uuid import uuid4

from fastapi import (
    APIRouter,
    BackgroundTasks,
    Body,
    FastAPI,
    Header,
    HTTPException,
    Request,
    Response,
    status,
)
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, ConfigDict, Field

from .artifacts import (
    ArtifactAssistantError,
    ArtifactAssistantMessage,
    ArtifactCitation,
    ArtifactGenerationError,
    ArtifactType,
    EvidenceCoverageError,
    StaleArtifactRevision,
    export_markdown,
    export_pdf,
    safe_export_filename,
)
from .discover.service import ChatRequest, DiscoverFilters, SearchRequest
from .models.contracts import ModelError
from .models.provider import ModelProvider
from .retrieval.citations import CitationResolver
from .runtime.mcp import MCPGateway
from .runtime.policy import ToolRegistry
from .services import ApplicationServices
from .settings import HighlandSettings
from .workflows import (
    PlannerRecord,
    WorkflowDefinition,
    WorkflowPlanner,
    WorkflowPlanningError,
)


class ApiModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class CreateConversationRequest(ApiModel):
    title: str | None = Field(default=None, max_length=200)


class RenameConversationRequest(ApiModel):
    title: str = Field(min_length=1, max_length=200)


class CreateMessageRequest(ApiModel):
    content: str = Field(min_length=1, max_length=100_000)


class CreateRunRequest(CreateMessageRequest):
    filters: DiscoverFilters = Field(default_factory=DiscoverFilters)


class CancelRunRequest(ApiModel):
    reason: str | None = Field(default=None, max_length=500)


class CreateArtifactRequest(ApiModel):
    title: str = Field(min_length=1, max_length=300)
    artifact_type: ArtifactType
    content: str = Field(max_length=1_000_000)
    conversation_id: str
    run_id: str
    message_id: str | None = None
    citations: list[ArtifactCitation] = Field(default_factory=list)


class UpdateArtifactRequest(ApiModel):
    expected_revision: int = Field(ge=1)
    title: str | None = Field(default=None, min_length=1, max_length=300)
    content: str | None = Field(default=None, max_length=1_000_000)
    citations: list[ArtifactCitation] | None = None
    reason: str = Field(default="manual edit", max_length=300)


class GenerateArtifactRequest(ApiModel):
    artifact_type: ArtifactType
    conversation_id: str
    message_id: str
    instructions: str | None = Field(default=None, max_length=20_000)


class ReviseArtifactSectionRequest(ApiModel):
    expected_revision: int = Field(ge=1)
    heading: str = Field(min_length=1, max_length=300)
    instructions: str = Field(min_length=1, max_length=20_000)


class PreviewArtifactAssistantEditRequest(ApiModel):
    expected_revision: int = Field(ge=1)
    instruction: str = Field(min_length=1, max_length=20_000)
    history: list[ArtifactAssistantMessage] = Field(default_factory=list, max_length=12)
    draft_content: str | None = Field(default=None, min_length=1, max_length=1_000_000)


class DraftWorkflowRequest(ApiModel):
    goal: str = Field(min_length=1, max_length=20_000)


class SaveWorkflowRequest(ApiModel):
    workflow: WorkflowDefinition
    planner: PlannerRecord | None = None


class RunWorkflowRequest(ApiModel):
    version: int | None = Field(default=None, ge=1)
    test: bool = True
    trigger: dict[str, object] = Field(default_factory=dict)
    workflow: WorkflowDefinition | None = None


class PublishWorkflowRequest(ApiModel):
    workflow: WorkflowDefinition | None = None


class ScheduleWorkflowRequest(ApiModel):
    version: int = Field(ge=1)
    interval_seconds: int = Field(ge=60)


def register_api_routes(app: FastAPI, services: ApplicationServices) -> None:
    """Register the four HTTP teaching surfaces without constructing dependencies."""
    configured = services.settings
    workspace = services.workspace
    approvals = services.approvals
    run_events = services.run_events
    cancellations = services.cancellations
    conversations = services.conversations
    artifacts = services.artifacts
    artifact_assistant = services.artifact_assistant
    provider = services.provider
    artifact_generator = services.artifact_generator
    coverage_checker = services.coverage_checker
    workflows = services.workflows
    workflow_runs = services.workflow_runs
    workflow_schedules = services.workflow_schedules
    discover = services.discover
    platform = APIRouter(tags=["platform operations"])
    workflow_routes = APIRouter(tags=["workflows"])
    artifact_routes = APIRouter(tags=["artifacts"])
    discover_routes = APIRouter(tags=["discover and runs"])

    @platform.get("/health")
    async def health() -> dict[str, str]:
        return {"status": "ok", "model_backend": configured.model_backend.value}

    @platform.get("/workspace/status")
    async def workspace_status() -> dict[str, object]:
        manifest = services.synchronizer().status()
        return {
            "workspace": configured.workspace_name,
            "model_mode": configured.model_backend.value,
            "models": services.effective_models(),
            "limits": services.effective_limits(),
            "index": {
                "state": manifest.state.value if manifest else "missing",
                "records": len(manifest.records) if manifest else 0,
            },
            "connectors": [
                {"name": name, "state": "configured"}
                for name in sorted(configured.connector_commands)
            ],
            "run": {"state": "idle"},
        }

    @platform.get("/agents")
    async def list_agents() -> list[dict[str, object]]:
        return [
            {
                **services.profile.model_dump(mode="json"),
                "models": services.effective_models(),
                "limits": services.effective_limits(),
            }
        ]

    @workflow_routes.post("/workflows/draft")
    async def draft_workflow(request: DraftWorkflowRequest) -> dict[str, object]:
        async with MCPGateway(
            configured.connector_commands,
            startup_timeout_seconds=configured.connector_timeout_seconds,
            request_timeout_seconds=configured.connector_timeout_seconds,
        ) as gateway:
            registry = ToolRegistry.from_file(gateway, configured.tool_policy_config)
            planner = WorkflowPlanner(
                provider.chat,
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

    @workflow_routes.post("/workflows", status_code=status.HTTP_201_CREATED)
    async def save_workflow(request: SaveWorkflowRequest) -> dict[str, object]:
        saved = workflows.save_draft(request.workflow)
        payload = saved.model_dump(mode="json")
        if request.planner:
            payload["planner"] = request.planner.model_dump(mode="json")
        return payload

    @workflow_routes.get("/workflows")
    async def list_workflows() -> list[dict[str, object]]:
        return [workflow.model_dump(mode="json") for workflow in workflows.list()]

    @workflow_routes.get("/workflows/{workflow_id}")
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

    @workflow_routes.post("/workflows/{workflow_id}/publish")
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

    @workflow_routes.post("/workflows/{workflow_id}/runs")
    async def run_workflow(workflow_id: str, request: RunWorkflowRequest) -> dict[str, object]:
        if request.test:
            if request.workflow is not None:
                if request.workflow.id != workflow_id:
                    raise HTTPException(
                        status_code=422,
                        detail="The test workflow ID does not match the requested workflow",
                    )
                definition = workflows.save_draft(request.workflow)
            else:
                try:
                    definition = workflows.get_draft(workflow_id)
                except FileNotFoundError:
                    raise HTTPException(
                        status_code=404,
                        detail="Workflow draft not found. Include or save the draft before testing.",
                    ) from None
            version = request.version or 0
        else:
            if request.workflow is not None:
                raise HTTPException(
                    status_code=422,
                    detail="Inline drafts can only be used for test runs",
                )
            if request.version is None:
                raise HTTPException(status_code=422, detail="Published version is required")
            try:
                published = workflows.get_version(workflow_id, request.version)
            except FileNotFoundError:
                raise HTTPException(status_code=404, detail="Workflow version not found") from None
            definition, version = published.definition, published.version
        run_id = f"wrun_{uuid4().hex}"
        run_events.append(
            run_id,
            "run_started",
            {"workflow_id": workflow_id, "workflow_version": version, "test": request.test},
        )
        async with MCPGateway(
            configured.connector_commands,
            startup_timeout_seconds=configured.connector_timeout_seconds,
            request_timeout_seconds=configured.connector_timeout_seconds,
        ) as gateway:
            registry = ToolRegistry.from_file(gateway, configured.tool_policy_config)
            run = await services.workflow_executor(registry).run(
                definition,
                run_id=run_id,
                workflow_version=version,
                trigger=request.trigger,
            )
        for node in run.nodes.values():
            event_type = (
                "model_call"
                if definition.model_dump()["nodes"][
                    [item.id for item in definition.nodes].index(node.node_id)
                ]["kind"]
                == "generate"
                else "tool_call"
            )
            run_events.append(
                run_id,
                event_type,
                {
                    "node_id": node.node_id,
                    "status": node.status.value,
                    "attempts": node.attempts,
                    "duration_ms": node.duration_ms,
                    "usage": node.usage.model_dump(mode="json"),
                },
            )
        terminal_event = (
            "run_completed"
            if run.status.value == "completed"
            else "approval_required"
            if run.status.value == "paused"
            else "run_failed"
        )
        run_events.append(
            run_id,
            terminal_event,
            {
                "workflow_id": workflow_id,
                "workflow_version": version,
                "status": run.status.value,
            },
        )
        return {
            **run.model_dump(mode="json"),
            "trace_url": f"/runs/{run_id}/trace",
        }

    @workflow_routes.get("/workflow-runs")
    async def list_workflow_runs() -> list[dict[str, object]]:
        return [run.model_dump(mode="json") for run in workflow_runs.list()]

    @workflow_routes.post("/workflows/{workflow_id}/schedules", status_code=status.HTTP_201_CREATED)
    async def schedule_workflow(
        workflow_id: str, request: ScheduleWorkflowRequest
    ) -> dict[str, object]:
        try:
            published = workflows.get_version(workflow_id, request.version)
        except FileNotFoundError:
            raise HTTPException(
                status_code=422, detail="Only a published workflow version can be scheduled"
            ) from None
        return workflow_schedules.create(
            published, interval_seconds=request.interval_seconds
        ).model_dump(mode="json")

    @artifact_routes.post("/artifacts", status_code=status.HTTP_201_CREATED)
    async def create_artifact(request: CreateArtifactRequest) -> dict[str, object]:
        try:
            conversation = conversations.get(request.conversation_id)
        except FileNotFoundError:
            raise HTTPException(
                status_code=404, detail="Originating conversation not found"
            ) from None
        if not any(message.run_id == request.run_id for message in conversation.messages):
            raise HTTPException(
                status_code=422,
                detail="Originating run does not belong to the conversation",
            )
        artifact = artifacts.create(**request.model_dump())
        return artifact.model_dump(mode="json")

    @artifact_routes.post("/artifacts/generate", status_code=status.HTTP_201_CREATED)
    async def generate_artifact(request: GenerateArtifactRequest) -> dict[str, object]:
        try:
            conversation = conversations.get(request.conversation_id)
        except FileNotFoundError:
            raise HTTPException(
                status_code=404, detail="Originating conversation not found"
            ) from None
        message = next(
            (item for item in conversation.messages if item.id == request.message_id),
            None,
        )
        if message is None or message.role != "assistant" or not message.run_id:
            raise HTTPException(status_code=422, detail="Select a completed assistant answer")
        evidence = [
            ArtifactCitation(
                id=f"E{index}",
                label=str(index),
                source_id=source.source_id,
                source_url=source.source_url,
                chunk_id=source.chunk_id,
                title=source.title,
                passage=source.passage,
                source_system=source.source_system,
                updated_at=source.updated_at,
            )
            for index, source in enumerate(message.sources, start=1)
        ]
        try:
            artifact = await artifact_generator.generate(
                artifact_type=request.artifact_type,
                answer=message.content,
                evidence=evidence,
                conversation_id=request.conversation_id,
                run_id=message.run_id,
                message_id=message.id,
                instructions=request.instructions,
            )
        except ArtifactGenerationError as error:
            raise HTTPException(status_code=422, detail=str(error)) from error
        except ModelError as error:
            raise HTTPException(
                status_code=status.HTTP_502_BAD_GATEWAY,
                detail=f"Artifact generation model failed: {error}",
            ) from error
        return artifact.model_dump(mode="json")

    @artifact_routes.get("/artifacts")
    async def list_artifacts() -> list[dict[str, object]]:
        return [artifact.model_dump(mode="json") for artifact in artifacts.list()]

    @artifact_routes.get("/artifacts/{artifact_id}")
    async def get_artifact(artifact_id: str) -> dict[str, object]:
        try:
            return artifacts.get(artifact_id).model_dump(mode="json")
        except FileNotFoundError:
            raise HTTPException(status_code=404, detail="Artifact not found") from None

    @artifact_routes.post("/artifacts/{artifact_id}/evidence-coverage")
    async def check_artifact_evidence(artifact_id: str) -> dict[str, object]:
        try:
            report = await coverage_checker.check(artifacts.get(artifact_id))
        except FileNotFoundError:
            raise HTTPException(status_code=404, detail="Artifact not found") from None
        except EvidenceCoverageError as error:
            raise HTTPException(status_code=422, detail=str(error)) from error
        except ModelError as error:
            raise HTTPException(
                status_code=status.HTTP_502_BAD_GATEWAY,
                detail=f"Evidence coverage model failed: {error}",
            ) from error
        return report.model_dump(mode="json")

    @artifact_routes.post("/artifacts/{artifact_id}/assistant/preview")
    async def preview_artifact_assistant_edit(
        artifact_id: str,
        request: PreviewArtifactAssistantEditRequest,
    ) -> dict[str, object]:
        try:
            preview = await artifact_assistant.preview_edit(
                artifact_id,
                expected_revision=request.expected_revision,
                instruction=request.instruction,
                history=request.history,
                draft_content=request.draft_content,
            )
        except FileNotFoundError:
            raise HTTPException(status_code=404, detail="Artifact not found") from None
        except ArtifactAssistantError as error:
            raise HTTPException(status_code=422, detail=str(error)) from error
        except ModelError as error:
            raise HTTPException(
                status_code=status.HTTP_502_BAD_GATEWAY,
                detail=f"Artifact assistant model failed: {error}",
            ) from error
        return preview.model_dump(mode="json")

    @artifact_routes.get("/artifacts/{artifact_id}/export.md")
    async def export_artifact_markdown(artifact_id: str) -> Response:
        try:
            artifact = artifacts.get(artifact_id)
        except FileNotFoundError:
            raise HTTPException(status_code=404, detail="Artifact not found") from None
        filename = safe_export_filename(artifact.title, extension="md")
        return Response(
            content=export_markdown(artifact),
            media_type="text/markdown; charset=utf-8",
            headers={"Content-Disposition": f'attachment; filename="{filename}"'},
        )

    @artifact_routes.get("/artifacts/{artifact_id}/export.pdf")
    async def export_artifact_pdf(artifact_id: str) -> Response:
        try:
            artifact = artifacts.get(artifact_id)
        except FileNotFoundError:
            raise HTTPException(status_code=404, detail="Artifact not found") from None
        filename = safe_export_filename(artifact.title, extension="pdf")
        return Response(
            content=export_pdf(artifact),
            media_type="application/pdf",
            headers={"Content-Disposition": f'attachment; filename="{filename}"'},
        )

    @artifact_routes.patch("/artifacts/{artifact_id}")
    async def update_artifact(
        artifact_id: str, request: UpdateArtifactRequest
    ) -> dict[str, object]:
        try:
            artifact = artifacts.update(
                artifact_id,
                **request.model_dump(exclude_none=True),
            )
        except FileNotFoundError:
            raise HTTPException(status_code=404, detail="Artifact not found") from None
        except StaleArtifactRevision as error:
            raise HTTPException(status_code=409, detail=str(error)) from error
        return artifact.model_dump(mode="json")

    @artifact_routes.post("/artifacts/{artifact_id}/sections/revise")
    async def revise_artifact_section(
        artifact_id: str,
        request: ReviseArtifactSectionRequest,
    ) -> dict[str, object]:
        try:
            preview = await artifact_generator.preview_section_revision(
                artifact_id,
                **request.model_dump(),
            )
        except FileNotFoundError:
            raise HTTPException(status_code=404, detail="Artifact not found") from None
        except ArtifactGenerationError as error:
            raise HTTPException(status_code=409, detail=str(error)) from error
        except ModelError as error:
            raise HTTPException(
                status_code=status.HTTP_502_BAD_GATEWAY,
                detail=f"Artifact revision model failed: {error}",
            ) from error
        return preview.model_dump(mode="json")

    @artifact_routes.delete("/artifacts/{artifact_id}", status_code=status.HTTP_204_NO_CONTENT)
    async def delete_artifact(artifact_id: str) -> Response:
        try:
            artifacts.delete(artifact_id)
        except FileNotFoundError:
            raise HTTPException(status_code=404, detail="Artifact not found") from None
        return Response(status_code=status.HTTP_204_NO_CONTENT)

    @artifact_routes.get("/artifacts/{artifact_id}/revisions")
    async def list_artifact_revisions(artifact_id: str) -> list[dict[str, object]]:
        try:
            return [
                revision.model_dump(mode="json") for revision in artifacts.revisions(artifact_id)
            ]
        except FileNotFoundError:
            raise HTTPException(status_code=404, detail="Artifact not found") from None

    @artifact_routes.get("/artifacts/{artifact_id}/revisions/{revision}")
    async def get_artifact_revision(artifact_id: str, revision: int) -> dict[str, object]:
        try:
            return artifacts.revision(artifact_id, revision).model_dump(mode="json")
        except FileNotFoundError:
            raise HTTPException(status_code=404, detail="Artifact revision not found") from None

    @platform.get("/index/status")
    async def index_status() -> dict[str, object]:
        manifest = services.synchronizer().status()
        return (
            manifest.model_dump(mode="json")
            if manifest
            else {"state": "missing", "records": {}, "source_counts": {}}
        )

    @platform.post("/index/sync")
    async def index_sync() -> dict[str, object]:
        return (await services.synchronizer().sync()).model_dump(mode="json")

    @platform.post("/index/rebuild")
    async def index_rebuild() -> dict[str, object]:
        return (await services.synchronizer().rebuild()).model_dump(mode="json")

    @platform.get("/index/chunks/{chunk_id}")
    async def inspect_chunk(chunk_id: str) -> dict[str, object]:
        chunk = CitationResolver(workspace.indexes / "search").get_chunk(chunk_id)
        if chunk is None:
            raise HTTPException(status_code=404, detail="Indexed chunk not found")
        return chunk.model_dump(mode="json")

    @discover_routes.post("/conversations", status_code=status.HTTP_201_CREATED)
    async def create_conversation(
        request: CreateConversationRequest | None = None,
    ) -> dict[str, object]:
        conversation = conversations.create(request.title if request else None)
        return conversation.model_dump(mode="json")

    @discover_routes.get("/conversations")
    async def list_conversations() -> list[dict[str, object]]:
        return [item.model_dump(mode="json") for item in conversations.list()]

    @discover_routes.get("/conversations/{conversation_id}")
    async def get_conversation(conversation_id: str) -> dict[str, object]:
        try:
            return conversations.get(conversation_id).model_dump(mode="json")
        except FileNotFoundError:
            raise HTTPException(status_code=404, detail="Conversation not found") from None

    @discover_routes.patch("/conversations/{conversation_id}")
    async def rename_conversation(
        conversation_id: str,
        request: RenameConversationRequest,
    ) -> dict[str, object]:
        try:
            return conversations.rename(conversation_id, request.title).model_dump(mode="json")
        except FileNotFoundError:
            raise HTTPException(status_code=404, detail="Conversation not found") from None

    @discover_routes.delete(
        "/conversations/{conversation_id}", status_code=status.HTTP_204_NO_CONTENT
    )
    async def delete_conversation(conversation_id: str) -> Response:
        try:
            conversations.delete(conversation_id)
        except FileNotFoundError:
            raise HTTPException(status_code=404, detail="Conversation not found") from None
        return Response(status_code=status.HTTP_204_NO_CONTENT)

    @discover_routes.post(
        "/conversations/{conversation_id}/messages", status_code=status.HTTP_201_CREATED
    )
    async def add_message(
        conversation_id: str,
        request: CreateMessageRequest,
    ) -> dict[str, object]:
        try:
            return conversations.append_message(
                conversation_id, role="user", content=request.content
            ).model_dump(mode="json")
        except FileNotFoundError:
            raise HTTPException(status_code=404, detail="Conversation not found") from None

    @discover_routes.post(
        "/conversations/{conversation_id}/runs", status_code=status.HTTP_202_ACCEPTED
    )
    async def create_run(
        conversation_id: str,
        request: CreateRunRequest,
        background_tasks: BackgroundTasks,
    ) -> dict[str, object]:
        run_id = f"run_{uuid4().hex}"
        try:
            conversation = conversations.get(conversation_id)
            if conversation.messages:
                raise HTTPException(
                    status_code=status.HTTP_409_CONFLICT,
                    detail=(
                        "Discovery runs require a fresh conversation. "
                        "Create a new conversation before starting another run."
                    ),
                )
            message = conversations.append_message(
                conversation_id,
                role="user",
                content=request.content,
                run_id=run_id,
            )
        except FileNotFoundError:
            raise HTTPException(status_code=404, detail="Conversation not found") from None
        run_events.append(
            run_id,
            "run_started",
            {"conversation_id": conversation_id, "message_id": message.id},
        )
        background_tasks.add_task(
            _run_discovery,
            ChatRequest(
                conversation_id=conversation_id,
                query=request.content,
                filters=request.filters,
            ),
            run_id,
        )
        return {
            "run_id": run_id,
            "conversation_id": conversation_id,
            "message_id": message.id,
            "status": "queued",
            "events_url": f"/runs/{run_id}/events",
        }

    async def _run_discovery(request: ChatRequest, run_id: str) -> None:
        try:
            await discover.chat(request, run_id=run_id)
        except Exception as error:  # noqa: BLE001 - persist background failure for the UI
            payload: dict[str, object] = {
                "error_type": type(error).__name__,
                "message": str(error),
            }
            if isinstance(error, ModelError):
                payload.update(
                    {
                        "code": error.code,
                        "provider": error.provider,
                        "request_id": error.request_id,
                        "retryable": error.retryable,
                    }
                )
            run_events.append(
                run_id,
                "error",
                payload,
            )

    @discover_routes.post("/runs/{run_id}/cancel", status_code=status.HTTP_202_ACCEPTED)
    async def cancel_run(
        run_id: str,
        request: CancelRunRequest | None = None,
    ) -> dict[str, object]:
        payload = cancellations.cancel(run_id, reason=request.reason if request else None)
        if run_events.summary(run_id)["status"] not in {"completed", "failed", "cancelled"}:
            run_events.append(run_id, "run_cancelled", payload)
        return {**payload, "status": "cancelled"}

    @discover_routes.post("/discover/search")
    async def discover_search(request: SearchRequest) -> dict[str, object]:
        try:
            result = await discover.search_sources(request)
        except FileNotFoundError as error:
            raise HTTPException(status_code=409, detail=str(error)) from error
        return result.model_dump(mode="json")

    @discover_routes.post("/discover/chat")
    async def discover_chat(request: ChatRequest) -> dict[str, object]:
        run_id = f"run_{uuid4().hex}"
        try:
            message = conversations.append_message(
                request.conversation_id,
                role="user",
                content=request.query,
                run_id=run_id,
            )
            outcome = await discover.chat(request, run_id=run_id)
            conversation = conversations.get(request.conversation_id)
        except FileNotFoundError as error:
            raise HTTPException(status_code=404, detail=str(error)) from error
        return {
            **outcome.model_dump(mode="json"),
            "conversation_id": request.conversation_id,
            "message_id": message.id,
            "sources": [
                source.model_dump(mode="json") for source in conversation.messages[-1].sources
            ],
        }

    @platform.get("/approvals/{approval_id}")
    async def get_approval(approval_id: str) -> dict[str, object]:
        try:
            return approvals.get(approval_id).model_dump(mode="json")
        except FileNotFoundError:
            raise HTTPException(status_code=404, detail="Approval not found") from None

    @platform.post("/approvals/{approval_id}/approve")
    async def approve(
        approval_id: str,
        reason: str | None = Body(default=None, embed=True),
    ) -> dict[str, object]:
        try:
            return approvals.decide(approval_id, approve=True, reason=reason).model_dump(
                mode="json"
            )
        except FileNotFoundError:
            raise HTTPException(status_code=404, detail="Approval not found") from None
        except ValueError as error:
            raise HTTPException(status_code=409, detail=str(error)) from error

    @platform.post("/approvals/{approval_id}/reject")
    async def reject(
        approval_id: str,
        reason: str | None = Body(default=None, embed=True),
    ) -> dict[str, object]:
        try:
            return approvals.decide(approval_id, approve=False, reason=reason).model_dump(
                mode="json"
            )
        except FileNotFoundError:
            raise HTTPException(status_code=404, detail="Approval not found") from None
        except ValueError as error:
            raise HTTPException(status_code=409, detail=str(error)) from error

    @discover_routes.get("/runs/{run_id}/summary")
    async def run_summary(run_id: str) -> dict[str, object]:
        return run_events.summary(run_id)

    @discover_routes.get("/runs")
    async def list_runs() -> list[dict[str, object]]:
        return run_events.list_summaries()

    @discover_routes.get("/runs/{run_id}/trace")
    async def run_trace(run_id: str) -> list[dict[str, object]]:
        return [event.model_dump(mode="json") for event in run_events.replay(run_id)]

    @discover_routes.get("/runs/{run_id}/evidence")
    async def run_evidence(run_id: str) -> dict[str, object]:
        events = run_events.replay(run_id)
        retrieval = next(
            (event.payload for event in events if event.type.value == "retrieval"),
            {},
        )
        citations = [event.payload for event in events if event.type.value == "citation"]
        source_ids = {
            source_id
            for citation in citations
            for source_id in citation.get("source_ids", [])
            if isinstance(source_id, str)
        }
        resolver = CitationResolver(workspace.indexes / "search")
        evidence = []
        for chunk_id in sorted(source_ids):
            chunk = resolver.get_chunk(chunk_id)
            if chunk is not None:
                evidence.append(chunk.model_dump(mode="json"))
        refreshed = any(event.type.value == "tool_result" for event in events)
        return {
            "run_id": run_id,
            "filters": retrieval.get("filters", {}),
            "citations": citations,
            "evidence": evidence,
            "diagnostics": retrieval.get("diagnostics", []),
            "timings": retrieval.get("timings", {}),
            "refreshed_through_mcp": refreshed,
        }

    @discover_routes.get("/runs/{run_id}/events")
    async def stream_run_events(
        run_id: str,
        request: Request,
        last_event_id: int = Header(default=0, alias="Last-Event-ID"),
    ) -> StreamingResponse:
        if run_events.summary(run_id)["status"] == "missing":
            raise HTTPException(status_code=404, detail="Run not found")

        async def stream():
            cursor = last_event_id
            heartbeat_at = asyncio.get_running_loop().time()
            terminal_types = {"error", "final", "run_cancelled", "run_completed", "run_failed"}
            while True:
                if await request.is_disconnected():
                    return
                events = run_events.replay(run_id, after_id=cursor)
                for event in events:
                    cursor = event.id
                    yield run_events.sse(event)
                    if event.type.value in terminal_types:
                        return
                summary = run_events.summary(run_id)
                if (
                    summary["status"] in {"completed", "failed", "cancelled"}
                    and cursor >= int(summary["last_event_id"])
                ):
                    return
                now = asyncio.get_running_loop().time()
                if now - heartbeat_at >= 15:
                    yield ": keep-alive\n\n"
                    heartbeat_at = now
                await asyncio.sleep(0.1)

        return StreamingResponse(
            stream(),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
        )

    app.include_router(discover_routes)
    app.include_router(artifact_routes)
    app.include_router(workflow_routes)
    app.include_router(platform)


def create_app(
    settings: HighlandSettings | None = None,
    *,
    model_provider: ModelProvider | None = None,
) -> FastAPI:
    configured = settings or HighlandSettings()
    services = ApplicationServices.build(configured, model_provider=model_provider)
    app = FastAPI(title="Highland", version="0.1.0")
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["http://127.0.0.1:3000", "http://localhost:3000"],
        allow_methods=["*"],
        allow_headers=["*"],
    )
    app.state.services = services
    register_api_routes(app, services)
    return app


app = create_app()
