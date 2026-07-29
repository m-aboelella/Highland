from __future__ import annotations

from pathlib import Path

import pytest

from highland.artifacts import (
    ArtifactCitation,
    ArtifactGenerationError,
    ArtifactGenerator,
    ArtifactRepository,
    ArtifactType,
)
from highland.models.contracts import ChatResponse, FinishReason, Message, MessageRole
from highland.models.scripted import ScriptedChatModel, simulated_metadata


def response(output: object) -> ChatResponse:
    return ChatResponse(
        message=Message(role=MessageRole.ASSISTANT),
        finish_reason=FinishReason.COMPLETE,
        metadata=simulated_metadata("artifact-test"),
        structured_output=output,
    )


def evidence() -> list[ArtifactCitation]:
    return [
        ArtifactCitation(
            id="E1",
            label="1",
            source_id="doc_1",
            source_url="mock://archive/doc_1",
            passage="Latency rose to 800 ms.",
        )
    ]


@pytest.mark.asyncio
async def test_generation_uses_structured_model_prose_and_valid_evidence(tmp_path: Path) -> None:
    model = ScriptedChatModel(
        [
            response(
                {
                    "title": "Incident review",
                    "sections": [{"heading": "Impact", "purpose": "Summarize impact"}],
                }
            ),
            response(
                {
                    "title": "Incident review",
                    "sections": [
                        {
                            "heading": "Impact",
                            "markdown": "Latency rose to 800 ms [E1].",
                            "citation_ids": ["E1"],
                        }
                    ],
                }
            ),
        ]
    )
    generator = ArtifactGenerator(model, ArtifactRepository(tmp_path))
    artifact = await generator.generate(
        artifact_type=ArtifactType.INCIDENT_REPORT,
        answer="Latency rose.",
        evidence=evidence(),
        conversation_id="con_1",
        run_id="run_1",
    )

    assert "800 ms [E1]" in artifact.content
    assert [item.id for item in artifact.citations] == ["E1"]
    assert [request.logical_call_id for request in model.requests] == [
        "artifact-section-plan",
        "artifact-cited-draft",
    ]
    assert model.requests[1].response_schema is not None


@pytest.mark.asyncio
async def test_generation_rejects_invented_citation_id(tmp_path: Path) -> None:
    model = ScriptedChatModel(
        [
            response({"title": "Brief", "sections": [{"heading": "Fact", "purpose": "Fact"}]}),
            response(
                {
                    "title": "Brief",
                    "sections": [
                        {"heading": "Fact", "markdown": "Invented [E9].", "citation_ids": ["E9"]}
                    ],
                }
            ),
        ]
    )
    with pytest.raises(ArtifactGenerationError, match="Unknown evidence IDs"):
        await ArtifactGenerator(model, ArtifactRepository(tmp_path)).generate(
            artifact_type=ArtifactType.BRIEFING,
            answer="An answer",
            evidence=evidence(),
            conversation_id="con_1",
            run_id="run_1",
        )


@pytest.mark.asyncio
async def test_section_revision_preserves_unselected_sections(tmp_path: Path) -> None:
    repository = ArtifactRepository(tmp_path)
    artifact = repository.create(
        title="Review",
        artifact_type=ArtifactType.INCIDENT_REPORT,
        content="## Impact\n\nOld impact [E1].\n\n## Cause\n\nCause unchanged [E1].\n",
        citations=evidence(),
        conversation_id="con_1",
        run_id="run_1",
    )
    model = ScriptedChatModel(
        [
            response(
                {
                    "heading": "Impact",
                    "markdown": "New impact [E1].",
                    "citation_ids": ["E1"],
                    "invalidated_citation_ids": [],
                }
            )
        ]
    )
    preview = await ArtifactGenerator(model, repository).preview_section_revision(
        artifact.id,
        expected_revision=1,
        heading="Impact",
        instructions="Make it clearer",
    )

    assert "New impact" in preview.resulting_content
    assert "Cause unchanged [E1]." in preview.resulting_content
    assert repository.get(artifact.id).content == artifact.content
