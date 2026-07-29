from __future__ import annotations

from pathlib import Path

import pytest

from highland.artifacts import ArtifactCitation, ArtifactGenerator, ArtifactRepository, ArtifactType
from highland.models.contracts import ChatResponse, FinishReason, Message, MessageRole
from highland.models.scripted import ScriptedChatModel, simulated_metadata


@pytest.mark.integration
@pytest.mark.asyncio
async def test_model_artifact_flow_persists_structured_draft(tmp_path: Path) -> None:
    def result(value: object) -> ChatResponse:
        return ChatResponse(
            message=Message(role=MessageRole.ASSISTANT),
            finish_reason=FinishReason.COMPLETE,
            metadata=simulated_metadata("integration-artifact"),
            structured_output=value,
        )

    model = ScriptedChatModel(
        [
            result({"title": "Brief", "sections": [{"heading": "Summary", "purpose": "Summary"}]}),
            result(
                {
                    "title": "Brief",
                    "sections": [
                        {
                            "heading": "Summary",
                            "markdown": "| Metric | Value |\n| --- | --- |\n| Latency | 800 ms [E1] |",
                            "citation_ids": ["E1"],
                        }
                    ],
                }
            ),
        ]
    )
    repository = ArtifactRepository(tmp_path)
    artifact = await ArtifactGenerator(model, repository).generate(
        artifact_type=ArtifactType.BRIEFING,
        answer="Latency was 800 ms.",
        evidence=[
            ArtifactCitation(
                id="E1",
                label="1",
                source_id="metric_1",
                source_url="mock://beacon/metric_1",
                passage="Latency was 800 ms.",
            )
        ],
        conversation_id="con_1",
        run_id="run_1",
    )
    assert "| Latency | 800 ms [E1] |" in repository.get(artifact.id).content
