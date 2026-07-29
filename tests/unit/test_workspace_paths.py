from __future__ import annotations

from highland.workspace import WorkspacePaths


def test_workspace_can_be_created_and_reset(tmp_path) -> None:
    mock_state = tmp_path / "mock-system.json"
    mock_state.write_text("preserve me", encoding="utf-8")
    workspace = WorkspacePaths.from_root(tmp_path / "platform")

    workspace.ensure()
    (workspace.conversations / "conversation.json").write_text("{}", encoding="utf-8")
    (workspace.indexes / "nested").mkdir()
    workspace.reset()

    assert all(path.is_dir() for path in workspace.state_directories)
    assert not (workspace.conversations / "conversation.json").exists()
    assert mock_state.read_text(encoding="utf-8") == "preserve me"
