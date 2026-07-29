from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient

from highland.api import create_app
from highland.artifacts import ArtifactRepository, ArtifactType, StaleArtifactRevision
from highland.settings import HighlandSettings


def test_repository_persists_human_readable_revisions_and_rejects_stale_updates(
    tmp_path: Path,
) -> None:
    repository = ArtifactRepository(tmp_path / "artifacts")
    artifact = repository.create(
        title="Northwind briefing",
        artifact_type=ArtifactType.BRIEFING,
        content="# Northwind\n\nRenewal is approaching.",
        conversation_id="con_origin",
        run_id="run_origin",
    )
    updated = repository.update(
        artifact.id,
        expected_revision=1,
        content="# Northwind\n\nRenewal is approaching in Q4.",
    )

    restarted = ArtifactRepository(tmp_path / "artifacts")
    assert restarted.get(artifact.id).content.endswith("in Q4.")
    assert (tmp_path / "artifacts" / artifact.id / "content.md").read_text("utf-8").startswith("#")
    assert [item.revision for item in restarted.revisions(artifact.id)] == [1, 2]
    try:
        restarted.update(artifact.id, expected_revision=1, content="stale")
    except StaleArtifactRevision as error:
        assert "revision 2" in str(error)
    else:
        raise AssertionError("stale update was accepted")
    assert updated.conversation_id == "con_origin"
    assert updated.run_id == "run_origin"


def test_artifact_crud_api_links_origin_and_survives_restart(tmp_path: Path) -> None:
    settings = HighlandSettings(workspace_dir=tmp_path)
    with TestClient(create_app(settings)) as client:
        conversation = client.post("/conversations").json()
        message = client.post(
            f"/conversations/{conversation['id']}/messages",
            json={"content": "Prepare a briefing"},
        ).json()
        # Attach an originating run through the regular persisted conversation contract.
        from highland.discover.conversations import ConversationStore

        store = ConversationStore(tmp_path / "conversations")
        persisted = store.get(conversation["id"])
        persisted.messages[0].run_id = "run_briefing"
        store.save(persisted)
        created_response = client.post(
            "/artifacts",
            json={
                "title": "Briefing",
                "artifact_type": "briefing",
                "content": "# Briefing",
                "conversation_id": conversation["id"],
                "run_id": "run_briefing",
                "message_id": message["id"],
            },
        )
        assert created_response.status_code == 201
        created = created_response.json()
        stale = client.patch(
            f"/artifacts/{created['id']}",
            json={"expected_revision": 7, "content": "stale"},
        )
        assert stale.status_code == 409
        saved = client.patch(
            f"/artifacts/{created['id']}",
            json={"expected_revision": 1, "content": "# Edited"},
        ).json()
        assert saved["revision"] == 2

    with TestClient(create_app(settings)) as restarted:
        assert restarted.get(f"/artifacts/{created['id']}").json()["content"] == "# Edited"
        assert len(restarted.get(f"/artifacts/{created['id']}/revisions").json()) == 2
        assert restarted.delete(f"/artifacts/{created['id']}").status_code == 204
