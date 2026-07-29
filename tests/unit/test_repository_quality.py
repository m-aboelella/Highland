from pathlib import Path

from highland.repository_quality import generated_path_errors, secret_errors


def test_generated_runtime_paths_are_rejected() -> None:
    errors = generated_path_errors(
        [
            Path("var/highland/runs/example.json"),
            Path("web/.next/build.json"),
            Path("src/highland/__pycache__/api.pyc"),
            Path("web/tsconfig.tsbuildinfo"),
        ]
    )

    assert len(errors) == 4


def test_secret_scanner_flags_key_but_allows_documented_placeholder(tmp_path: Path) -> None:
    leaked = tmp_path / "leaked.txt"
    leaked.write_text(f"COHERE_API_KEY={'a' * 32}", encoding="utf-8")
    example = tmp_path / "example.txt"
    example.write_text("COHERE_API_KEY=...", encoding="utf-8")

    assert secret_errors([Path("leaked.txt")], tmp_path)
    assert secret_errors([Path("example.txt")], tmp_path) == []
