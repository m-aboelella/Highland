import json
import stat
import tomllib
from pathlib import Path

ROOT = Path(__file__).parents[2]


def test_debug_dependency_and_single_command_are_checked_in() -> None:
    project = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    assert any(
        item.startswith("debugpy") for item in project["project"]["optional-dependencies"]["dev"]
    )
    assert "debugpy==" in (ROOT / "requirements-dev.lock").read_text(encoding="utf-8")

    makefile = (ROOT / "Makefile").read_text(encoding="utf-8")
    assert "debug:\n\t./scripts/debug.sh" in makefile

    launcher = ROOT / "scripts" / "debug.sh"
    assert launcher.stat().st_mode & stat.S_IXUSR
    launcher_text = launcher.read_text(encoding="utf-8")
    assert "-Xfrozen_modules=off scripts/debug_app.py" in launcher_text
    assert "HighlandSettings().model_backend.value" in launcher_text
    assert "HIGHLAND_DEBUG_MODEL_BACKEND:-$configured_backend" in launcher_text
    assert "Scripted chat needs a predefined response queue" in launcher_text
    assert "A Highland debug session is already listening" in launcher_text
    assert "HIGHLAND_DEBUG_REBUILD:-0" in launcher_text
    assert "docker compose stop highland-api" in launcher_text

    debug_entrypoint = (ROOT / "scripts" / "debug_app.py").read_text(encoding="utf-8")
    assert "debugpy.wait_for_client()" in debug_entrypoint
    assert "debugpy.configure(subProcess=True)" in debug_entrypoint


def test_vscode_workspace_can_attach_to_the_debug_command() -> None:
    extensions = json.loads((ROOT / ".vscode" / "extensions.json").read_text(encoding="utf-8"))
    assert {"ms-python.python", "ms-python.debugpy"} <= set(extensions["recommendations"])

    launch = json.loads((ROOT / ".vscode" / "launch.json").read_text(encoding="utf-8"))
    attach = next(
        item
        for item in launch["configurations"]
        if item["name"] == "Highland: Attach to make debug"
    )
    assert attach["type"] == "debugpy"
    assert attach["request"] == "attach"
    assert attach["connect"] == {"host": "127.0.0.1", "port": 5678}
    assert attach["subProcess"] is True


def test_readme_documents_the_debugger_hand_off() -> None:
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    for required in (
        "make debug",
        "Highland: Attach to make debug",
        "127.0.0.1:5678",
        "HIGHLAND_DEBUG_REBUILD=1 make debug",
        "HIGHLAND_DEBUG_MODEL_BACKEND=scripted make debug",
        "HIGHLAND_DEBUG_MODEL_BACKEND=cohere make debug",
        "arbitrary interactive Discover prompt has no queued response",
    ):
        assert required in readme
