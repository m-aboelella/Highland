import json
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
        assert test_run.json()["test"] is True
        assert test_run.json()["workflow_snapshot"]["id"] == "wf_builder"
        assert client.get("/workflows/wf_builder").status_code == 200
        assert client.get("/workflows/wf_builder").json()["versions"] == []

        run_id = test_run.json()["id"]
        persisted = client.get(f"/workflow-runs/{run_id}")
        assert persisted.status_code == 200
        assert persisted.json()["nodes"] == test_run.json()["nodes"]

        published = client.post(f"/workflow-runs/{run_id}/publish")
        assert published.json()["version"] == 1
        assert published.json()["source_run_id"] == run_id
        assert client.get(f"/workflow-runs/{run_id}").json()["published_version"] == 1
        published_workflows = client.get("/published-workflows").json()
        assert published_workflows == [
            {
                "workflow_id": "wf_builder",
                "name": "Builder",
                "description": "",
                "latest_version": 1,
                "version_count": 1,
                "published_at": published.json()["published_at"],
                "step_count": 1,
                "active_schedule_count": 0,
            }
        ]
        history = client.get("/workflow-runs").json()
        assert history[0]["workflow_id"] == "wf_builder"
        assert history[0]["workflow_version"] == 0
        assert client.get(f"/runs/{history[0]['id']}/trace").status_code == 200

        deleted = client.delete("/workflows/wf_builder")
        assert deleted.status_code == 204
        assert client.get("/workflows/wf_builder").status_code == 404
        assert client.get("/published-workflows").json() == []
        assert client.get(f"/workflow-runs/{run_id}").status_code == 200


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


def test_failed_and_older_test_runs_are_not_publishable(tmp_path: Path) -> None:
    workflow = WorkflowDefinition(
        id="wf_old_run",
        name="Old run",
        nodes=[TriggerNode(id="start", name="Start")],
    )
    with TestClient(
        create_app(HighlandSettings(workspace_dir=tmp_path, connector_commands={}))
    ) as client:
        run = client.post(
            "/workflows/wf_old_run/runs",
            json={"test": True, "workflow": workflow.model_dump(mode="json")},
        ).json()
        run_path = tmp_path / "runs" / "workflows" / f"{run['id']}.json"
        stored = json.loads(run_path.read_text("utf-8"))
        stored["workflow_snapshot"] = None
        run_path.write_text(
            json.dumps(stored),
            encoding="utf-8",
        )

        response = client.post(f"/workflow-runs/{run['id']}/publish")
        assert response.status_code == 422
        assert "older test run" in response.json()["detail"]
