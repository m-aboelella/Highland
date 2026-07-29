from __future__ import annotations

import shutil
from typing import Any

import httpx

from .settings import HighlandSettings
from .workspace import WorkspacePaths


def inspect_environment(settings: HighlandSettings) -> dict[str, Any]:
    workspace = WorkspacePaths.from_root(settings.workspace_dir)
    workspace.ensure()

    connectors = {}
    for name, command in settings.connector_commands.items():
        executable = command[0] if command else ""
        connectors[name] = {
            "command": list(command),
            "executable_found": bool(executable and shutil.which(executable)),
        }

    mock_reachable = False
    mock_error: str | None = None
    try:
        response = httpx.get(
            settings.mock_catalog_url,
            timeout=min(settings.request_timeout_seconds, 2.0),
        )
        mock_reachable = response.is_success
        if not response.is_success:
            mock_error = f"HTTP {response.status_code}"
    except httpx.HTTPError as error:
        mock_error = error.__class__.__name__

    return {
        "workspace": {
            "path": str(workspace.root),
            "ready": all(path.is_dir() for path in workspace.state_directories),
        },
        "connectors": connectors,
        "mock_services": {
            "catalog_url": settings.mock_catalog_url,
            "reachable": mock_reachable,
            "error": mock_error,
        },
        "models": {
            "backend": settings.model_backend.value,
            "cohere_key_configured": settings.cohere_api_key is not None,
        },
    }
