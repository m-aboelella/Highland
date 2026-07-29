from __future__ import annotations

from uuid import uuid4

from fastapi import BackgroundTasks, Body, FastAPI, Header, HTTPException, Response, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, ConfigDict, Field

from .discover.conversations import ConversationStore
from .discover.service import ChatRequest, DiscoverFilters, DiscoverService, SearchRequest
from .models.provider import ModelProvider, build_model_provider
from .retrieval.citations import CitationResolver
from .retrieval.sources import MCPSourceReader
from .retrieval.sync import IndexSynchronizer
from .runtime.agent import AgentProfile
from .runtime.approvals import ApprovalStore
from .runtime.cancellation import RunCancellationStore
from .runtime.events import RunEventStore
from .settings import HighlandSettings
from .workspace import WorkspacePaths


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


def create_app(
    settings: HighlandSettings | None = None,
    *,
    model_provider: ModelProvider | None = None,
) -> FastAPI:
    configured = settings or HighlandSettings()
    workspace = WorkspacePaths.from_root(configured.workspace_dir)
    workspace.ensure()
    app = FastAPI(title="Highland", version="0.1.0")
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["http://127.0.0.1:3000", "http://localhost:3000"],
        allow_methods=["*"],
        allow_headers=["*"],
    )
    approvals = ApprovalStore(workspace.runs / "approvals")
    run_events = RunEventStore(workspace.runs / "events")
    cancellations = RunCancellationStore(workspace.runs / "cancellations")
    conversations = ConversationStore(workspace.conversations)
    provider = model_provider or build_model_provider(configured)
    discover = DiscoverService(
        index_dir=workspace.indexes / "search",
        conversations=conversations,
        runs_dir=workspace.runs,
        provider=provider,
        profile=AgentProfile.load(configured.agent_profile_config),
        tool_policy=configured.tool_policy_config,
        connector_commands=configured.connector_commands,
        connector_timeout_seconds=configured.connector_timeout_seconds,
    )

    @app.get("/health")
    async def health() -> dict[str, str]:
        return {"status": "ok", "model_backend": configured.model_backend.value}

    @app.get("/workspace/status")
    async def workspace_status() -> dict[str, object]:
        manifest = synchronizer().status()
        return {
            "workspace": configured.workspace_name,
            "model_mode": configured.model_backend.value,
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

    @app.get("/agents")
    async def list_agents() -> list[dict[str, object]]:
        profile = AgentProfile.load(configured.agent_profile_config)
        return [profile.model_dump(mode="json")]

    def synchronizer() -> IndexSynchronizer:
        return IndexSynchronizer(
            MCPSourceReader(
                configured.connector_commands,
                timeout_seconds=configured.connector_timeout_seconds,
            ),
            index_dir=workspace.indexes / "search",
            reports_dir=workspace.synchronization,
            embedding_model=provider.embeddings,
        )

    @app.get("/index/status")
    async def index_status() -> dict[str, object]:
        manifest = synchronizer().status()
        return (
            manifest.model_dump(mode="json")
            if manifest
            else {"state": "missing", "records": {}, "source_counts": {}}
        )

    @app.post("/index/sync")
    async def index_sync() -> dict[str, object]:
        return (await synchronizer().sync()).model_dump(mode="json")

    @app.post("/index/rebuild")
    async def index_rebuild() -> dict[str, object]:
        return (await synchronizer().rebuild()).model_dump(mode="json")

    @app.get("/index/chunks/{chunk_id}")
    async def inspect_chunk(chunk_id: str) -> dict[str, object]:
        chunk = CitationResolver(workspace.indexes / "search").get_chunk(chunk_id)
        if chunk is None:
            raise HTTPException(status_code=404, detail="Indexed chunk not found")
        return chunk.model_dump(mode="json")

    @app.post("/conversations", status_code=status.HTTP_201_CREATED)
    async def create_conversation(
        request: CreateConversationRequest | None = None,
    ) -> dict[str, object]:
        conversation = conversations.create(request.title if request else None)
        return conversation.model_dump(mode="json")

    @app.get("/conversations")
    async def list_conversations() -> list[dict[str, object]]:
        return [item.model_dump(mode="json") for item in conversations.list()]

    @app.get("/conversations/{conversation_id}")
    async def get_conversation(conversation_id: str) -> dict[str, object]:
        try:
            return conversations.get(conversation_id).model_dump(mode="json")
        except FileNotFoundError:
            raise HTTPException(status_code=404, detail="Conversation not found") from None

    @app.patch("/conversations/{conversation_id}")
    async def rename_conversation(
        conversation_id: str,
        request: RenameConversationRequest,
    ) -> dict[str, object]:
        try:
            return conversations.rename(conversation_id, request.title).model_dump(mode="json")
        except FileNotFoundError:
            raise HTTPException(status_code=404, detail="Conversation not found") from None

    @app.delete("/conversations/{conversation_id}", status_code=status.HTTP_204_NO_CONTENT)
    async def delete_conversation(conversation_id: str) -> Response:
        try:
            conversations.delete(conversation_id)
        except FileNotFoundError:
            raise HTTPException(status_code=404, detail="Conversation not found") from None
        return Response(status_code=status.HTTP_204_NO_CONTENT)

    @app.post("/conversations/{conversation_id}/messages", status_code=status.HTTP_201_CREATED)
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

    @app.post("/conversations/{conversation_id}/runs", status_code=status.HTTP_202_ACCEPTED)
    async def create_run(
        conversation_id: str,
        request: CreateRunRequest,
        background_tasks: BackgroundTasks,
    ) -> dict[str, object]:
        run_id = f"run_{uuid4().hex}"
        try:
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
            run_events.append(
                run_id,
                "error",
                {"error_type": type(error).__name__, "message": str(error)},
            )

    @app.post("/runs/{run_id}/cancel", status_code=status.HTTP_202_ACCEPTED)
    async def cancel_run(
        run_id: str,
        request: CancelRunRequest | None = None,
    ) -> dict[str, object]:
        payload = cancellations.cancel(run_id, reason=request.reason if request else None)
        if run_events.summary(run_id)["status"] not in {"completed", "failed", "cancelled"}:
            run_events.append(run_id, "run_cancelled", payload)
        return {**payload, "status": "cancelled"}

    @app.post("/discover/search")
    async def discover_search(request: SearchRequest) -> dict[str, object]:
        try:
            result = await discover.search(request)
        except FileNotFoundError as error:
            raise HTTPException(status_code=409, detail=str(error)) from error
        return result.model_dump(mode="json")

    @app.post("/discover/chat")
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
                source.model_dump(mode="json")
                for source in conversation.messages[-1].sources
            ],
        }

    @app.get("/approvals/{approval_id}")
    async def get_approval(approval_id: str) -> dict[str, object]:
        try:
            return approvals.get(approval_id).model_dump(mode="json")
        except FileNotFoundError:
            raise HTTPException(status_code=404, detail="Approval not found") from None

    @app.post("/approvals/{approval_id}/approve")
    async def approve(
        approval_id: str,
        reason: str | None = Body(default=None, embed=True),
    ) -> dict[str, object]:
        try:
            return approvals.decide(approval_id, approve=True, reason=reason).model_dump(mode="json")
        except FileNotFoundError:
            raise HTTPException(status_code=404, detail="Approval not found") from None
        except ValueError as error:
            raise HTTPException(status_code=409, detail=str(error)) from error

    @app.post("/approvals/{approval_id}/reject")
    async def reject(
        approval_id: str,
        reason: str | None = Body(default=None, embed=True),
    ) -> dict[str, object]:
        try:
            return approvals.decide(approval_id, approve=False, reason=reason).model_dump(mode="json")
        except FileNotFoundError:
            raise HTTPException(status_code=404, detail="Approval not found") from None
        except ValueError as error:
            raise HTTPException(status_code=409, detail=str(error)) from error

    @app.get("/runs/{run_id}/summary")
    async def run_summary(run_id: str) -> dict[str, object]:
        return run_events.summary(run_id)

    @app.get("/runs/{run_id}/trace")
    async def run_trace(run_id: str) -> list[dict[str, object]]:
        return [event.model_dump(mode="json") for event in run_events.replay(run_id)]

    @app.get("/runs/{run_id}/evidence")
    async def run_evidence(run_id: str) -> dict[str, object]:
        events = run_events.replay(run_id)
        retrieval = next(
            (event.payload for event in events if event.type.value == "retrieval"),
            {},
        )
        citations = [
            event.payload for event in events if event.type.value == "citation"
        ]
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

    @app.get("/runs/{run_id}/events")
    async def stream_run_events(
        run_id: str,
        last_event_id: int = Header(default=0, alias="Last-Event-ID"),
    ) -> StreamingResponse:
        async def stream():
            for event in run_events.replay(run_id, after_id=last_event_id):
                yield run_events.sse(event)

        return StreamingResponse(stream(), media_type="text/event-stream")

    return app


app = create_app()
