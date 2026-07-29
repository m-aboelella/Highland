from __future__ import annotations

import re
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
FORBIDDEN_PARTS = {
    ".mypy_cache",
    ".next",
    ".pytest_cache",
    ".ruff_cache",
    "__pycache__",
    "node_modules",
    "var",
}
FORBIDDEN_NAMES = {".env", ".coverage"}
FORBIDDEN_SUFFIXES = {".pyc", ".pyo", ".tsbuildinfo"}
SECRET_PATTERNS = {
    "private key": re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----"),
    "provider API key": re.compile(
        r"(?i)(?:cohere_api_key|api[_-]?key)[ \t]*[:=][ \t]*[\"']?"
        r"(?!\.\.\.|example|replace-me|your-|\$\{|<)[A-Za-z0-9_-]{20,}"
    ),
    "OpenAI-style secret": re.compile(r"\bsk-[A-Za-z0-9_-]{20,}\b"),
    "AWS access key": re.compile(r"\bAKIA[0-9A-Z]{16}\b"),
}


def tracked_paths(root: Path = ROOT) -> list[Path]:
    result = subprocess.run(
        ["git", "ls-files", "-z"],
        cwd=root,
        check=True,
        capture_output=True,
    )
    return [Path(value) for value in result.stdout.decode().split("\0") if value]


def generated_path_errors(paths: list[Path]) -> list[str]:
    errors = []
    for path in paths:
        if (
            path.name in FORBIDDEN_NAMES
            or path.suffix in FORBIDDEN_SUFFIXES
            or FORBIDDEN_PARTS.intersection(path.parts)
        ):
            errors.append(f"generated/runtime file is tracked: {path}")
    return errors


def secret_errors(paths: list[Path], root: Path = ROOT) -> list[str]:
    errors = []
    for path in paths:
        try:
            content = (root / path).read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        for name, pattern in SECRET_PATTERNS.items():
            if pattern.search(content):
                errors.append(f"possible {name} in tracked file: {path}")
    return errors


def check_repository(root: Path = ROOT) -> tuple[int, list[str]]:
    paths = tracked_paths(root)
    return len(paths), generated_path_errors(paths) + secret_errors(paths, root)
