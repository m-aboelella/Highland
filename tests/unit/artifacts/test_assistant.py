from __future__ import annotations

from pathlib import Path

import pytest

from highland.artifacts import (
    ArtifactAssistant,
    ArtifactAssistantMessage,
    ArtifactCitation,
    ArtifactRepository,
    ArtifactType,
)
from highland.models.contracts import (
    ChatResponse,
    FinishReason,
    Message,
    MessageRole,
    ToolCall,
)
from highland.models.scripted import ScriptedChatModel, simulated_metadata


def tool_response(call: ToolCall) -> ChatResponse:
    return ChatResponse(
        message=Message(role=MessageRole.ASSISTANT, tool_calls=[call]),
        finish_reason=FinishReason.TOOL_CALL,
        metadata=simulated_metadata("artifact-assistant-test"),
    )


@pytest.mark.asyncio
async def test_assistant_reads_artifact_and_evidence_before_staging_edit(tmp_path: Path) -> None:
    repository = ArtifactRepository(tmp_path)
    citation = ArtifactCitation(
        id="E1",
        label="1",
        source_id="doc_1",
        source_url="mock://archive/doc_1",
        title="Latency report",
        passage="Latency rose to 800 ms.",
        source_system="archive",
    )
    artifact = repository.create(
        title="Incident review",
        artifact_type=ArtifactType.INCIDENT_REPORT,
        content="## Impact\n\nLatency rose [E1].\n",
        citations=[citation],
        conversation_id="con_1",
        run_id="run_1",
    )
    model = ScriptedChatModel(
        [
            tool_response(ToolCall(id="read", name="read_artifact", arguments={})),
            tool_response(
                ToolCall(
                    id="evidence",
                    name="read_saved_evidence",
                    arguments={"evidence_ids": ["E1"]},
                )
            ),
            tool_response(
                ToolCall(
                    id="propose",
                    name="propose_markdown_edit",
                    arguments={
                        "summary": "Clarified the measured impact.",
                        "content": "## Impact\n\nLatency rose to 800 ms [E1].\n",
                        "citation_ids": ["E1"],
                    },
                )
            ),
        ]
    )

    preview = await ArtifactAssistant(model, repository).preview_edit(
        artifact.id,
        expected_revision=1,
        instruction="Make the impact more specific.",
    )

    assert preview.proposed_content == "## Impact\n\nLatency rose to 800 ms [E1].\n"
    assert [operation.tool for operation in preview.operations] == [
        "read_artifact",
        "read_saved_evidence",
        "propose_markdown_edit",
        "write_artifact_revision",
    ]
    assert preview.operations[-1].status == "awaiting_approval"
    assert repository.get(artifact.id).revision == 1
    assert repository.get(artifact.id).content == artifact.content
    assert model.requests[0].required_capabilities.tools
    assert {tool.name for tool in model.requests[0].tools} == {
        "read_artifact",
        "read_saved_evidence",
        "propose_markdown_edit",
    }
    assert "Latency rose [E1]" in model.requests[1].messages[-1].tool_results[0].content
    assert "Latency report" in model.requests[2].messages[-1].tool_results[0].content

    follow_up_model = ScriptedChatModel(
        [
            tool_response(ToolCall(id="reread", name="read_artifact", arguments={})),
            tool_response(
                ToolCall(
                    id="refine",
                    name="propose_markdown_edit",
                    arguments={
                        "summary": "Made the impact more concise.",
                        "content": "## Impact\n\nMeasured latency was 800 ms [E1].\n",
                        "citation_ids": ["E1"],
                    },
                )
            ),
        ]
    )
    refined = await ArtifactAssistant(follow_up_model, repository).preview_edit(
        artifact.id,
        expected_revision=1,
        instruction="Make that wording more concise.",
        history=[
            ArtifactAssistantMessage(role="user", content="Make the impact more specific."),
            ArtifactAssistantMessage(role="assistant", content=preview.assistant_message),
        ],
        draft_content=preview.proposed_content,
    )

    assert refined.proposed_content == "## Impact\n\nMeasured latency was 800 ms [E1].\n"
    assert refined.operations[0].label == "Read the working draft"
    assert "Latency rose to 800 ms" in (
        follow_up_model.requests[1].messages[-1].tool_results[0].content
    )
    assert repository.get(artifact.id).revision == 1
