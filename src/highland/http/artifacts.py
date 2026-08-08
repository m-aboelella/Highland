"""Artifact creation, editing, evidence, and export HTTP routes."""

from fastapi import APIRouter, HTTPException, Response, status

from highland.artifacts import (
    ArtifactAssistantError,
    ArtifactCitation,
    ArtifactGenerationError,
    EvidenceCoverageError,
    StaleArtifactRevision,
    export_markdown,
    export_pdf,
    safe_export_filename,
)
from highland.models.contracts import ModelError
from highland.services import ApplicationServices

from .models import (
    CreateArtifactRequest,
    GenerateArtifactRequest,
    PreviewArtifactAssistantEditRequest,
    ReviseArtifactSectionRequest,
    UpdateArtifactRequest,
)


def create_artifact_router(services: ApplicationServices) -> APIRouter:
    router = APIRouter(tags=["artifacts"])
    artifacts = services.artifacts

    @router.post("/artifacts", status_code=status.HTTP_201_CREATED)
    async def create_artifact(request: CreateArtifactRequest) -> dict[str, object]:
        try:
            conversation = services.conversations.get(request.conversation_id)
        except FileNotFoundError:
            raise HTTPException(
                status_code=404, detail="Originating conversation not found"
            ) from None
        if not any(message.run_id == request.run_id for message in conversation.messages):
            raise HTTPException(
                status_code=422,
                detail="Originating run does not belong to the conversation",
            )
        return artifacts.create(**request.model_dump()).model_dump(mode="json")

    @router.post("/artifacts/generate", status_code=status.HTTP_201_CREATED)
    async def generate_artifact(request: GenerateArtifactRequest) -> dict[str, object]:
        try:
            conversation = services.conversations.get(request.conversation_id)
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
            artifact = await services.artifact_generator.generate(
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
            raise _model_failure("Artifact generation", error) from error
        return artifact.model_dump(mode="json")

    @router.get("/artifacts")
    async def list_artifacts() -> list[dict[str, object]]:
        return [artifact.model_dump(mode="json") for artifact in artifacts.list()]

    @router.get("/artifacts/{artifact_id}")
    async def get_artifact(artifact_id: str) -> dict[str, object]:
        return _get_artifact(services, artifact_id).model_dump(mode="json")

    @router.post("/artifacts/{artifact_id}/evidence-coverage")
    async def check_artifact_evidence(artifact_id: str) -> dict[str, object]:
        try:
            report = await services.coverage_checker.check(_get_artifact(services, artifact_id))
        except EvidenceCoverageError as error:
            raise HTTPException(status_code=422, detail=str(error)) from error
        except ModelError as error:
            raise _model_failure("Evidence coverage", error) from error
        return report.model_dump(mode="json")

    @router.post("/artifacts/{artifact_id}/assistant/preview")
    async def preview_artifact_assistant_edit(
        artifact_id: str,
        request: PreviewArtifactAssistantEditRequest,
    ) -> dict[str, object]:
        try:
            preview = await services.artifact_assistant.preview_edit(
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
            raise _model_failure("Artifact assistant", error) from error
        return preview.model_dump(mode="json")

    @router.get("/artifacts/{artifact_id}/export.md")
    async def export_artifact_markdown(artifact_id: str) -> Response:
        artifact = _get_artifact(services, artifact_id)
        filename = safe_export_filename(artifact.title, extension="md")
        return Response(
            content=export_markdown(artifact),
            media_type="text/markdown; charset=utf-8",
            headers={"Content-Disposition": f'attachment; filename="{filename}"'},
        )

    @router.get("/artifacts/{artifact_id}/export.pdf")
    async def export_artifact_pdf(artifact_id: str) -> Response:
        artifact = _get_artifact(services, artifact_id)
        filename = safe_export_filename(artifact.title, extension="pdf")
        return Response(
            content=export_pdf(artifact),
            media_type="application/pdf",
            headers={"Content-Disposition": f'attachment; filename="{filename}"'},
        )

    @router.patch("/artifacts/{artifact_id}")
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

    @router.post("/artifacts/{artifact_id}/sections/revise")
    async def revise_artifact_section(
        artifact_id: str,
        request: ReviseArtifactSectionRequest,
    ) -> dict[str, object]:
        try:
            preview = await services.artifact_generator.preview_section_revision(
                artifact_id,
                **request.model_dump(),
            )
        except FileNotFoundError:
            raise HTTPException(status_code=404, detail="Artifact not found") from None
        except ArtifactGenerationError as error:
            raise HTTPException(status_code=409, detail=str(error)) from error
        except ModelError as error:
            raise _model_failure("Artifact revision", error) from error
        return preview.model_dump(mode="json")

    @router.delete("/artifacts/{artifact_id}", status_code=status.HTTP_204_NO_CONTENT)
    async def delete_artifact(artifact_id: str) -> Response:
        try:
            artifacts.delete(artifact_id)
        except FileNotFoundError:
            raise HTTPException(status_code=404, detail="Artifact not found") from None
        return Response(status_code=status.HTTP_204_NO_CONTENT)

    @router.get("/artifacts/{artifact_id}/revisions")
    async def list_artifact_revisions(artifact_id: str) -> list[dict[str, object]]:
        try:
            return [
                revision.model_dump(mode="json") for revision in artifacts.revisions(artifact_id)
            ]
        except FileNotFoundError:
            raise HTTPException(status_code=404, detail="Artifact not found") from None

    @router.get("/artifacts/{artifact_id}/revisions/{revision}")
    async def get_artifact_revision(artifact_id: str, revision: int) -> dict[str, object]:
        try:
            return artifacts.revision(artifact_id, revision).model_dump(mode="json")
        except FileNotFoundError:
            raise HTTPException(status_code=404, detail="Artifact revision not found") from None

    return router


def _get_artifact(services: ApplicationServices, artifact_id: str):
    try:
        return services.artifacts.get(artifact_id)
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail="Artifact not found") from None


def _model_failure(operation: str, error: ModelError) -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_502_BAD_GATEWAY,
        detail=f"{operation} model failed: {error}",
    )
