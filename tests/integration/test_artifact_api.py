from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient

from highland.api import create_app
from highland.models.contracts import ChatRequest, ModelError
from highland.settings import HighlandSettings


class FailingArtifactModel:
    async def chat(self, _request: ChatRequest):
        raise ModelError(
            "provider rejected the structured output request",
            code="invalid_request",
            provider="cohere",
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
