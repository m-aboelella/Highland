from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient

from highland.api import create_app
from highland.settings import HighlandSettings


def client_for(workspace: Path) -> TestClient:
    return TestClient(create_app(HighlandSettings(workspace_dir=workspace)))


def test_conversation_crud_survives_restart(tmp_path: Path) -> None:
    with client_for(tmp_path) as client:
        created = client.post("/conversations", json={"title": "Northwind"}).json()
        conversation_id = created["id"]
        assert client.patch(
            f"/conversations/{conversation_id}", json={"title": "Northwind review"}
        ).json()["title"] == "Northwind review"
        client.post(
            f"/conversations/{conversation_id}/messages",
            json={"content": "What changed?"},
        )

    with client_for(tmp_path) as restarted:
        loaded = restarted.get(f"/conversations/{conversation_id}").json()
        assert loaded["messages"][0]["content"] == "What changed?"
        assert restarted.get("/conversations").json()[0]["id"] == conversation_id


def test_run_is_tied_to_message_and_replayable_sse(tmp_path: Path) -> None:
    with client_for(tmp_path) as client:
        conversation_id = client.post("/conversations").json()["id"]
        created = client.post(
            f"/conversations/{conversation_id}/runs",
            json={"content": "Prepare the meeting."},
        )
        assert created.status_code == 202
        run = created.json()
        conversation = client.get(f"/conversations/{conversation_id}").json()
        assert conversation["active_run_id"] == run["run_id"]
        assert conversation["messages"][0]["run_id"] == run["run_id"]
        assert "event: run_started" in client.get(run["events_url"]).text


def test_delete_only_removes_conversation(tmp_path: Path) -> None:
    source = tmp_path / "indexes" / "search" / "chunks.jsonl"
    artifact = tmp_path / "artifacts" / "brief.md"
    source.parent.mkdir(parents=True)
    artifact.parent.mkdir(parents=True)
    source.write_text("source", encoding="utf-8")
    artifact.write_text("artifact", encoding="utf-8")
    with client_for(tmp_path) as client:
        conversation_id = client.post("/conversations").json()["id"]
        assert client.delete(f"/conversations/{conversation_id}").status_code == 204
        assert client.get(f"/conversations/{conversation_id}").status_code == 404
    assert source.read_text("utf-8") == "source"
    assert artifact.read_text("utf-8") == "artifact"
