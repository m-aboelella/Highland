import zipfile
from pathlib import Path

import pytest

from highland.maintenance import (
    export_learning_state,
    import_learning_state,
    reset_local_state,
    reset_targets,
)
from highland.settings import HighlandSettings


def _settings(tmp_path: Path, seed_dir: Path) -> HighlandSettings:
    return HighlandSettings(
        workspace_dir=tmp_path / "runtime" / "highland",
        seed_dir=seed_dir,
        runtime_dir=tmp_path / "runtime",
        connector_commands={},
    )


def test_export_import_round_trips_human_created_state(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    expected = {
        "artifacts/report.md": "# Human report\n",
        "workflows/wf.json": '{"id":"wf_human"}\n',
        "runs/events/run_1.events.jsonl": '{"type":"final"}\n',
    }
    for relative, content in expected.items():
        path = workspace / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
    archive = export_learning_state(workspace, tmp_path / "learning-state.zip")
    for relative in expected:
        (workspace / relative).unlink()

    targets = import_learning_state(workspace, archive, replace=True)

    assert targets == (
        workspace / "artifacts",
        workspace / "workflows",
        workspace / "runs" / "events",
    )
    for relative, content in expected.items():
        assert (workspace / relative).read_text(encoding="utf-8") == content


def test_import_refuses_to_replace_existing_state_without_opt_in(tmp_path: Path) -> None:
    source_workspace = tmp_path / "source"
    (source_workspace / "artifacts").mkdir(parents=True)
    (source_workspace / "artifacts" / "report.md").write_text("backup", encoding="utf-8")
    archive = export_learning_state(source_workspace, tmp_path / "backup.zip")
    target = tmp_path / "target"
    (target / "artifacts").mkdir(parents=True)
    (target / "artifacts" / "mine.md").write_text("keep", encoding="utf-8")

    with pytest.raises(FileExistsError, match="--replace"):
        import_learning_state(target, archive)

    assert (target / "artifacts" / "mine.md").read_text(encoding="utf-8") == "keep"


def test_import_rejects_archive_path_traversal(tmp_path: Path) -> None:
    archive = tmp_path / "unsafe.zip"
    with zipfile.ZipFile(archive, "w") as stream:
        stream.writestr("manifest.json", '{"format":"highland-learning-state","version":1}')
        stream.writestr("../outside.txt", "unsafe")

    with pytest.raises(ValueError, match="unsafe backup path"):
        import_learning_state(tmp_path / "workspace", archive)

    assert not (tmp_path / "outside.txt").exists()


def test_reset_restores_mock_files_without_touching_seed(
    tmp_path: Path, seed_dir: Path
) -> None:
    settings = _settings(tmp_path, seed_dir)
    original_seed = {
        path.name: path.read_text(encoding="utf-8") for path in seed_dir.glob("*.json")
    }
    settings.runtime_dir.mkdir(parents=True)
    (settings.runtime_dir / "support.json").write_text('{"mutated":true}', encoding="utf-8")
    artifact = settings.workspace_dir / "artifacts" / "mine.md"
    artifact.parent.mkdir(parents=True)
    artifact.write_text("remove", encoding="utf-8")

    targets = reset_local_state(settings)

    assert settings.runtime_dir / "support.json" in targets
    assert not artifact.exists()
    assert {
        path.name: path.read_text(encoding="utf-8") for path in seed_dir.glob("*.json")
    } == original_seed


def test_reset_rejects_repository_root_as_workspace(tmp_path: Path) -> None:
    settings = HighlandSettings(
        workspace_dir=Path(__file__).parents[2],
        seed_dir=tmp_path / "seed",
        runtime_dir=tmp_path / "runtime",
        connector_commands={},
    )

    with pytest.raises(ValueError, match="broad workspace"):
        reset_targets(settings)
