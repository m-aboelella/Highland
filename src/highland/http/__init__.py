"""FastAPI transport adapters grouped by Highland teaching surface."""

from .artifacts import create_artifact_router
from .discover import create_discover_router
from .platform import create_platform_router
from .workflows import create_workflow_router

__all__ = [
    "create_artifact_router",
    "create_discover_router",
    "create_platform_router",
    "create_workflow_router",
]
