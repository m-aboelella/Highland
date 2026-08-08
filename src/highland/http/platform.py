"""Operational, indexing, and approval HTTP routes."""

from fastapi import APIRouter, Body, HTTPException

from highland.retrieval.citations import CitationResolver
from highland.services import ApplicationServices


def create_platform_router(services: ApplicationServices) -> APIRouter:
    router = APIRouter(tags=["platform operations"])
    configured = services.settings

    @router.get("/health")
    async def health() -> dict[str, str]:
        return {"status": "ok", "model_backend": configured.model_backend.value}

    @router.get("/workspace/status")
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

    @router.get("/agents")
    async def list_agents() -> list[dict[str, object]]:
        return [
            {
                **services.profile.model_dump(mode="json"),
                "models": services.effective_models(),
                "limits": services.effective_limits(),
            }
        ]

    @router.get("/index/status")
    async def index_status() -> dict[str, object]:
        manifest = services.synchronizer().status()
        return (
            manifest.model_dump(mode="json")
            if manifest
            else {"state": "missing", "records": {}, "source_counts": {}}
        )

    @router.post("/index/sync")
    async def index_sync() -> dict[str, object]:
        return (await services.synchronizer().sync()).model_dump(mode="json")

    @router.post("/index/rebuild")
    async def index_rebuild() -> dict[str, object]:
        return (await services.synchronizer().rebuild()).model_dump(mode="json")

    @router.get("/index/chunks/{chunk_id}")
    async def inspect_chunk(chunk_id: str) -> dict[str, object]:
        chunk = CitationResolver(services.workspace.indexes / "search").get_chunk(chunk_id)
        if chunk is None:
            raise HTTPException(status_code=404, detail="Indexed chunk not found")
        return chunk.model_dump(mode="json")

    @router.get("/approvals/{approval_id}")
    async def get_approval(approval_id: str) -> dict[str, object]:
        try:
            return services.approvals.get(approval_id).model_dump(mode="json")
        except FileNotFoundError:
            raise HTTPException(status_code=404, detail="Approval not found") from None

    @router.post("/approvals/{approval_id}/approve")
    async def approve(
        approval_id: str,
        reason: str | None = Body(default=None, embed=True),
    ) -> dict[str, object]:
        return _decide_approval(services, approval_id, approve=True, reason=reason)

    @router.post("/approvals/{approval_id}/reject")
    async def reject(
        approval_id: str,
        reason: str | None = Body(default=None, embed=True),
    ) -> dict[str, object]:
        return _decide_approval(services, approval_id, approve=False, reason=reason)

    return router


def _decide_approval(
    services: ApplicationServices,
    approval_id: str,
    *,
    approve: bool,
    reason: str | None,
) -> dict[str, object]:
    try:
        return services.approvals.decide(
            approval_id,
            approve=approve,
            reason=reason,
        ).model_dump(mode="json")
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail="Approval not found") from None
    except ValueError as error:
        raise HTTPException(status_code=409, detail=str(error)) from error
