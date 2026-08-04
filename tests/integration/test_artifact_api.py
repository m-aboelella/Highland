from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient

from highland.api import create_app
from highland.models.contracts import (
    ChatRequest,
    ChatResponse,
    FinishReason,
    Message,
    MessageRole,
    ModelError,
    ToolCall,
)
from highland.models.scripted import ScriptedChatModel, simulated_metadata
from highland.settings import HighlandSettings


class FailingArtifactModel:
    async def chat(self, _request: ChatRequest):
        raise ModelError(
            "provider rejected the structured output request",
            code="invalid_request",
            provider="cohere",
        )


def tool_response(call: ToolCall) -> ChatResponse:
    return ChatResponse(
        message=Message(role=MessageRole.ASSISTANT, tool_calls=[call]),
        finish_reason=FinishReason.TOOL_CALL,
        metadata=simulated_metadata("artifact-api-test"),
    )


def test_artifact_model_failure_is_a_controlled_api_error(tmp_path: Path) -> None:
    app = create_app(HighlandSettings(workspace_dir=tmp_path))
    services = app.state.services
    conversation = services.conversations.create("Artifact failure")
    message = services.conversations.append_message(
        conversation.id,
        role="assistant",
        content="A completed grounded answer.",
        run_id="run_artifact",
    )
    services.artifact_generator.chat_model = FailingArtifactModel()

    with TestClient(app) as client:
        response = client.post(
            "/artifacts/generate",
            json={
                "artifact_type": "briefing",
                "conversation_id": conversation.id,
                "message_id": message.id,
            },
        )

    assert response.status_code == 502
    assert response.json()["detail"] == (
        "Artifact generation model failed: provider rejected the structured output request"
    )
    assert services.artifacts.list() == []


def test_artifact_assistant_model_failure_is_a_controlled_api_error(tmp_path: Path) -> None:
    app = create_app(HighlandSettings(workspace_dir=tmp_path))
    services = app.state.services
    artifact = services.artifacts.create(
        title="Artifact assistant failure",
        artifact_type="briefing",
        content="## Summary\n\nSaved content.\n",
        conversation_id="con_1",
        run_id="run_1",
    )
    services.artifact_assistant.chat_model = FailingArtifactModel()

    with TestClient(app) as client:
        response = client.post(
            f"/artifacts/{artifact.id}/assistant/preview",
            json={
                "expected_revision": 1,
                "instruction": "Make the summary clearer.",
                "history": [],
            },
        )

    assert response.status_code == 502
    assert response.json()["detail"] == (
        "Artifact assistant model failed: provider rejected the structured output request"
    )
    assert services.artifacts.get(artifact.id).revision == 1


def test_artifact_assistant_api_accepts_history_and_a_working_draft(tmp_path: Path) -> None:
    app = create_app(HighlandSettings(workspace_dir=tmp_path))
    services = app.state.services
    artifact = services.artifacts.create(
        title="Conversational edit",
        artifact_type="briefing",
        content="## Summary\n\nSaved text.\n",
        conversation_id="con_1",
        run_id="run_1",
    )
    services.artifact_assistant.chat_model = ScriptedChatModel([
        tool_response(ToolCall(id="read", name="read_artifact", arguments={})),
        tool_response(ToolCall(
            id="propose",
            name="propose_markdown_edit",
            arguments={
                "summary": "Refined the working draft.",
                "content": "## Summary\n\nRefined working draft.\n",
                "citation_ids": [],
            },
        )),
    ])

    with TestClient(app) as client:
        response = client.post(
            f"/artifacts/{artifact.id}/assistant/preview",
            json={
                "expected_revision": 1,
                "instruction": "Make that more concise.",
                "history": [
                    {"role": "user", "content": "Start a working draft."},
                    {"role": "assistant", "content": "Prepared a working draft."},
                ],
                "draft_content": "## Summary\n\nWorking draft.\n",
            },
        )

    assert response.status_code == 200
    assert response.json()["proposed_content"] == "## Summary\n\nRefined working draft.\n"
    assert response.json()["operations"][0]["label"] == "Read the working draft"
    assert services.artifacts.get(artifact.id).content == "## Summary\n\nSaved text.\n"
