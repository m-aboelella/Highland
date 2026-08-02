from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]


def test_compose_packages_complete_stack_with_one_runtime_mount() -> None:
    compose = (REPO_ROOT / "compose.yaml").read_text(encoding="utf-8")

    for service in (
        "catalog",
        "atlas-crm",
        "archive",
        "relay-desk",
        "beacon",
        "pulse",
        "track",
        "highland-bootstrap",
        "highland-api",
        "web",
    ):
        assert f"  {service}:" in compose
    assert compose.count("./var:/data/runtime") == 3
    assert "HIGHLAND_MODEL_BACKEND: ${HIGHLAND_MODEL_BACKEND:-scripted}" in compose
    assert "COHERE_API_KEY: ${COHERE_API_KEY:-}" in compose
    assert "condition: service_healthy" in compose
    assert 'command: ["highland", "index", "sync"]' in compose
    assert "condition: service_completed_successfully" in compose


def test_root_bootstrap_only_asks_for_a_key_and_waits_for_readiness() -> None:
    script = (REPO_ROOT / "bootstrap.sh").read_text(encoding="utf-8")

    assert "Cohere API key:" in script
    assert "HIGHLAND_MODEL_BACKEND=cohere" in script
    assert "COHERE_API_KEY=%s" in script
    assert "docker compose up --build --detach --wait" in script
    assert "http://localhost:3000" in script

    powershell = (REPO_ROOT / "bootstrap.ps1").read_text(encoding="utf-8")
    assert "Cohere API key" in powershell
    assert "HIGHLAND_MODEL_BACKEND=cohere" in powershell
    assert "COHERE_API_KEY=$cohereKey" in powershell
    assert "docker compose up --build --detach --wait" in powershell
    assert "http://localhost:3000" in powershell


def test_python_install_paths_use_checked_in_locks() -> None:
    makefile = (REPO_ROOT / "Makefile").read_text(encoding="utf-8")
    dockerfile = (REPO_ROOT / "docker" / "Dockerfile").read_text(encoding="utf-8")
    workflow = (REPO_ROOT / ".github" / "workflows" / "ci.yml").read_text(
        encoding="utf-8"
    )
    production_lock = (REPO_ROOT / "requirements.lock").read_text(encoding="utf-8")
    development_lock = (REPO_ROOT / "requirements-dev.lock").read_text(encoding="utf-8")

    assert "pip install -r requirements-dev.lock" in makefile
    assert "pip install --no-deps -e ." in makefile
    assert "pip-compile pyproject.toml --output-file=requirements.lock" in makefile
    assert "pip install --no-cache-dir -r requirements.lock" in dockerfile
    assert "pip install --no-cache-dir --no-deps ." in dockerfile
    assert "pip install -r requirements-dev.lock" in workflow
    assert "cohere==" in production_lock
    assert "mcp==" in production_lock
    assert "pytest==" not in production_lock
    assert "cohere==" in development_lock
    assert "mcp==" in development_lock
    assert "pytest==" in development_lock
    assert "pip-tools==" in development_lock


def test_ci_runs_compose_smoke_after_hermetic_quality() -> None:
    workflow = (REPO_ROOT / ".github" / "workflows" / "ci.yml").read_text(
        encoding="utf-8"
    )

    assert "  compose-smoke:" in workflow
    assert "needs: hermetic-quality" in workflow
    assert "timeout-minutes: 30" in workflow
    assert "run: make compose-smoke" in workflow


def test_compose_smoke_covers_index_api_search_ui_and_shutdown() -> None:
    script = (REPO_ROOT / "scripts" / "compose_smoke.sh").read_text(encoding="utf-8")

    assert "docker compose up --build --detach --wait" in script
    assert "highland-bootstrap" in script
    assert "http://127.0.0.1:8080/health" in script
    assert "http://127.0.0.1:8080/discover/search" in script
    assert '"http://web:3000"' in script
    assert "highland eval retrieval --enforce-baseline" in script
    assert "docker compose down" in script


def test_container_context_excludes_runtime_state_and_secrets() -> None:
    ignored = (REPO_ROOT / ".dockerignore").read_text(encoding="utf-8").splitlines()

    assert ".env" in ignored
    assert "var" in ignored
    assert "web/node_modules" in ignored
