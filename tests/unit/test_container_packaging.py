import subprocess
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


def test_production_image_declares_packaged_application_configuration() -> None:
    dockerfile = (REPO_ROOT / "docker" / "Dockerfile").read_text(encoding="utf-8")
    required_config = {
        "HIGHLAND_MODEL_PRICE_CONFIG": "config/model_prices.json",
        "HIGHLAND_TOOL_POLICY_CONFIG": "config/tool_policy.json",
        "HIGHLAND_AGENT_PROFILE_CONFIG": "config/agents/general.json",
    }

    assert "COPY config ./config" in dockerfile
    for setting, relative_path in required_config.items():
        assert f"ENV {setting}=/app/{relative_path}" in dockerfile
        assert (REPO_ROOT / relative_path).is_file()


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


def test_compose_smoke_preserves_scoped_failure_evidence_before_cleanup() -> None:
    script = (REPO_ROOT / "scripts" / "compose_smoke.sh").read_text(encoding="utf-8")

    failure_branch = script.index('if [ "$smoke_status" -ne 0 ]')
    service_state = script.index("docker compose ps --all", failure_branch)
    scoped_logs = script.index(
        "docker compose logs --no-color highland-bootstrap highland-api",
        failure_branch,
    )
    cleanup = script.index("docker compose down", failure_branch)

    assert failure_branch < service_state < scoped_logs < cleanup
    assert "docker compose config" not in script
    assert "docker inspect" not in script


def test_compose_smoke_has_valid_posix_shell_syntax() -> None:
    script = REPO_ROOT / "scripts" / "compose_smoke.sh"

    subprocess.run(
        ["sh", "-n", str(script)],
        check=True,
        capture_output=True,
        text=True,
    )


def test_container_context_excludes_runtime_state_and_secrets() -> None:
    ignored = (REPO_ROOT / ".dockerignore").read_text(encoding="utf-8").splitlines()

    assert ".env" in ignored
    assert "var" in ignored
    assert "web/node_modules" in ignored
