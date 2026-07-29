from __future__ import annotations

from datetime import UTC, datetime

import pytest

from highland.artifacts import (
    Artifact,
    ArtifactCitation,
    ArtifactType,
    ClaimSupport,
    EvidenceCoverageChecker,
    EvidenceCoverageError,
)
from highland.models.contracts import ChatResponse, FinishReason, Message, MessageRole
from highland.models.scripted import ScriptedChatModel, simulated_metadata


def response(claims: list[dict[str, object]]) -> ChatResponse:
    return ChatResponse(
        message=Message(role=MessageRole.ASSISTANT),
        finish_reason=FinishReason.COMPLETE,
        metadata=simulated_metadata("coverage-test"),
        structured_output={"claims": claims},
    )


def artifact() -> Artifact:
    return Artifact(
        id="art_coverage",
        title="Report",
        artifact_type=ArtifactType.INCIDENT_REPORT,
        content="Latency reached 800 ms. The root cause is unknown. Revenue fell.",
        citations=[
            ArtifactCitation(
                id="E1",
                label="1",
                source_id="metric_1",
                source_url="mock://beacon/metric_1",
                passage="Latency reached 800 ms.",
                updated_at=datetime(2026, 7, 28, tzinfo=UTC),
            ),
            ArtifactCitation(
                id="E2",
                label="2",
                source_id="old_doc",
                source_url="mock://archive/old_doc",
                passage="The root cause is unknown.",
                updated_at=datetime(2025, 1, 1, tzinfo=UTC),
            ),
        ],
        conversation_id="con_1",
        run_id="run_1",
    )


@pytest.mark.asyncio
async def test_marks_supported_weak_unsupported_and_stale_claims() -> None:
    content = artifact().content
    claims = [
        {
            "text": "Latency reached 800 ms.",
            "start": content.index("Latency"),
            "end": content.index("Latency") + len("Latency reached 800 ms."),
            "citation_ids": ["E1"],
            "model_assessment": "supported",
        },
        {
            "text": "The root cause is unknown.",
            "start": content.index("The root"),
            "end": content.index("The root") + len("The root cause is unknown."),
            "citation_ids": ["E2"],
            "model_assessment": "supported",
        },
        {
            "text": "Revenue fell.",
            "start": content.index("Revenue"),
            "end": len(content),
            "citation_ids": [],
            "model_assessment": "unsupported",
        },
    ]
    report = await EvidenceCoverageChecker(ScriptedChatModel([response(claims)])).check(
        artifact(),
        checked_at=datetime(2026, 7, 29, tzinfo=UTC),
    )
    assert [claim.status for claim in report.claims] == [
        ClaimSupport.SUPPORTED,
        ClaimSupport.STALE,
        ClaimSupport.UNSUPPORTED,
    ]
    assert "not mathematical guarantees" in report.advisory


@pytest.mark.asyncio
async def test_preserves_model_weak_assessment_after_validating_mapping() -> None:
    content = artifact().content
    report = await EvidenceCoverageChecker(
        ScriptedChatModel(
            [
                response(
                    [
                        {
                            "text": "Latency reached 800 ms.",
                            "start": 0,
                            "end": len("Latency reached 800 ms."),
                            "citation_ids": ["E1"],
                            "model_assessment": "weak",
                        }
                    ]
                )
            ]
        )
    ).check(artifact())
    assert report.claims[0].status is ClaimSupport.WEAKLY_SUPPORTED
    assert content.startswith(report.claims[0].text)


@pytest.mark.asyncio
async def test_rejects_invented_citation_ids_and_invalid_spans() -> None:
    bad_id = response(
        [{
            "text": "Latency reached 800 ms.",
            "start": 0,
            "end": len("Latency reached 800 ms."),
            "citation_ids": ["E99"],
            "model_assessment": "supported",
        }]
    )
    with pytest.raises(EvidenceCoverageError, match="Unknown evidence IDs"):
        await EvidenceCoverageChecker(ScriptedChatModel([bad_id])).check(artifact())

    bad_span = response(
        [{
            "text": "Latency reached 800 ms.",
            "start": 1,
            "end": len("Latency reached 800 ms.") + 1,
            "citation_ids": ["E1"],
            "model_assessment": "supported",
        }]
    )
    with pytest.raises(EvidenceCoverageError, match="Invalid span"):
        await EvidenceCoverageChecker(ScriptedChatModel([bad_span])).check(artifact())
