from __future__ import annotations

import re
import tomllib
from pathlib import Path

from packaging.requirements import Requirement
from packaging.utils import canonicalize_name
from packaging.version import Version

ROOT = Path(__file__).resolve().parents[2]
PACKAGE = "highland-educational (pyproject.toml)"
PIN = re.compile(r"^([A-Za-z0-9_.-]+)==([^ ;]+)")


def declared_dependencies(*, include_dev: bool) -> dict[str, Requirement]:
    project = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))["project"]
    values = list(project["dependencies"])
    if include_dev:
        values.extend(project["optional-dependencies"]["dev"])
    return {canonicalize_name(item.name): item for item in map(Requirement, values)}


def locked_dependencies(path: Path) -> tuple[dict[str, Version], set[str]]:
    pins: dict[str, Version] = {}
    direct: set[str] = set()
    current: str | None = None
    for line in path.read_text(encoding="utf-8").splitlines():
        match = PIN.match(line)
        if match:
            current = canonicalize_name(match.group(1))
            pins[current] = Version(match.group(2))
        elif current and PACKAGE in line:
            direct.add(current)
    return pins, direct


def check_lock(path: Path, *, include_dev: bool) -> list[str]:
    declared = declared_dependencies(include_dev=include_dev)
    pins, direct = locked_dependencies(path)
    errors: list[str] = []
    if direct != set(declared):
        missing = sorted(set(declared) - direct)
        stale = sorted(direct - set(declared))
        if missing:
            errors.append(f"{path.name}: missing direct dependencies: {', '.join(missing)}")
        if stale:
            errors.append(f"{path.name}: stale direct dependencies: {', '.join(stale)}")
    for name, requirement in declared.items():
        version = pins.get(name)
        if version is None:
            continue
        if requirement.specifier and version not in requirement.specifier:
            errors.append(
                f"{path.name}: {name}=={version} does not satisfy {requirement.specifier}"
            )
    return errors


def main() -> None:
    errors = [
        *check_lock(ROOT / "requirements.lock", include_dev=False),
        *check_lock(ROOT / "requirements-dev.lock", include_dev=True),
    ]
    if errors:
        raise SystemExit("\n".join(errors) + "\nRun `make lock` and commit both lock files.")
    print("dependency lock check passed")
