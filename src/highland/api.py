from __future__ import annotations

from fastapi import FastAPI

from .models.provider import build_model_provider
from .retrieval.sources import MCPSourceReader
from .retrieval.sync import IndexSynchronizer
from .settings import HighlandSettings
from .workspace import WorkspacePaths


def create_app(settings: HighlandSettings | None = None) -> FastAPI:
    configured = settings or HighlandSettings()
    workspace = WorkspacePaths.from_root(configured.workspace_dir)
    workspace.ensure()
    app = FastAPI(title="Highland", version="0.1.0")

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

    return app


app = create_app()
