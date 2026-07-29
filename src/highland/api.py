from __future__ import annotations

from fastapi import Body, FastAPI, Header, HTTPException
from fastapi.responses import StreamingResponse

from .models.provider import build_model_provider
from .retrieval.citations import CitationResolver
from .retrieval.sources import MCPSourceReader
from .retrieval.sync import IndexSynchronizer
from .runtime.approvals import ApprovalStore
from .runtime.events import RunEventStore
from .settings import HighlandSettings
from .workspace import WorkspacePaths


def create_app(settings: HighlandSettings | None = None) -> FastAPI:
    configured = settings or HighlandSettings()
    workspace = WorkspacePaths.from_root(configured.workspace_dir)
    workspace.ensure()
    app = FastAPI(title="Highland", version="0.1.0")
    approvals = ApprovalStore(workspace.runs / "approvals")
    run_events = RunEventStore(workspace.runs / "events")

    @app.get("/health")
    async def health() -> dict[str, str]:
        return {"status": "ok", "model_backend": configured.model_backend.value}

    def synchronizer() -> IndexSynchronizer:
        return IndexSynchronizer(
            MCPSourceReader(
                configured.connector_commands,
                timeout_seconds=configured.connector_timeout_seconds,
            ),
            index_dir=workspace.indexes / "search",
            reports_dir=workspace.synchronization,
            embedding_model=build_model_provider(configured).embeddings,
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
