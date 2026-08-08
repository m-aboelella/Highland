"""Highland FastAPI application composition."""

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from .http import (
    create_artifact_router,
    create_discover_router,
    create_platform_router,
    create_workflow_router,
)
from .models.provider import ModelProvider
from .services import ApplicationServices
from .settings import HighlandSettings


def register_api_routes(app: FastAPI, services: ApplicationServices) -> None:
    """Register the four HTTP teaching surfaces."""
    app.include_router(create_discover_router(services))
    app.include_router(create_artifact_router(services))
    app.include_router(create_workflow_router(services))
    app.include_router(create_platform_router(services))


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
