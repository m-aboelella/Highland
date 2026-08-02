from pathlib import Path

from highland.dependency_locks import check_lock

ROOT = Path(__file__).resolve().parents[2]


def test_production_and_development_locks_match_pyproject() -> None:
    assert check_lock(ROOT / "requirements.lock", include_dev=False) == []
    assert check_lock(ROOT / "requirements-dev.lock", include_dev=True) == []


def test_lock_check_detects_a_missing_direct_dependency(tmp_path: Path) -> None:
    incomplete = tmp_path / "requirements.lock"
    incomplete.write_text("cohere==7.0.8\n    # via another-package\n", encoding="utf-8")

    errors = check_lock(incomplete, include_dev=False)

    assert errors
    assert "missing direct dependencies" in errors[0]
