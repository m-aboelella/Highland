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
        "highland-api",
        "web",
    ):
        assert f"  {service}:" in compose
    assert compose.count("./var:/data/runtime") == 2
    assert "HIGHLAND_MODEL_BACKEND: ${HIGHLAND_MODEL_BACKEND:-scripted}" in compose
    assert "COHERE_API_KEY: ${COHERE_API_KEY:-}" in compose
    assert 'condition: service_healthy' in compose


def test_container_context_excludes_runtime_state_and_secrets() -> None:
    ignored = (REPO_ROOT / ".dockerignore").read_text(encoding="utf-8").splitlines()

    assert ".env" in ignored
    assert "var" in ignored
    assert "web/node_modules" in ignored
