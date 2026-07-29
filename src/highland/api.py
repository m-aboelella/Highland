from __future__ import annotations

from fastapi import FastAPI

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

    return app


app = create_app()
