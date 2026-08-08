from pathlib import Path

from fastapi.testclient import TestClient

from highland.api import create_app
from highland.settings import HighlandSettings
from highland.workflows import NodeInput, TriggerNode, WorkflowDefinition


def test_builder_api_keeps_test_draft_separate_from_published_versions(tmp_path: Path) -> None:
    workflow = WorkflowDefinition(
        id="wf_builder",
        name="Builder",
        nodes=[TriggerNode(id="start", name="Start", inputs={"goal": NodeInput(value="test")})],
    )
    with TestClient(
        create_app(HighlandSettings(workspace_dir=tmp_path, connector_commands={}))
    ) as client:
        test_run = client.post(
            "/workflows/wf_builder/runs",
            json={"test": True, "workflow": workflow.model_dump(mode="json")},
        )
        assert test_run.status_code == 200
        assert test_run.json()["workflow_version"] == 0
        assert client.get("/workflows/wf_builder").status_code == 200
        assert client.get("/workflows/wf_builder").json()["versions"] == []

        published = client.post("/workflows/wf_builder/publish")
        assert published.json()["version"] == 1
        history = client.get("/workflow-runs").json()
        assert history[0]["workflow_id"] == "wf_builder"
        assert history[0]["workflow_version"] == 0
        assert client.get(f"/runs/{history[0]['id']}/trace").status_code == 200


def test_publish_api_publishes_the_current_inline_draft(tmp_path: Path) -> None:
    workflow = WorkflowDefinition(
        id="wf_publish_inline",
        name="Current browser draft",
        nodes=[TriggerNode(id="start", name="Start")],
    )
    with TestClient(
        create_app(HighlandSettings(workspace_dir=tmp_path, connector_commands={}))
    ) as client:
        published = client.post(
            "/workflows/wf_publish_inline/publish",
            json={"workflow": workflow.model_dump(mode="json")},
        )

        assert published.status_code == 200
        assert published.json()["version"] == 1
        assert published.json()["definition"]["name"] == "Current browser draft"
        retried = client.post(
            "/workflows/wf_publish_inline/publish",
            json={"workflow": workflow.model_dump(mode="json")},
        )
        assert retried.status_code == 200
        assert retried.json()["version"] == 1
        assert len(client.get("/workflows/wf_publish_inline").json()["versions"]) == 1
        assert client.get("/workflows/wf_publish_inline").json()["draft"]["name"] == (
            "Current browser draft"
        )
