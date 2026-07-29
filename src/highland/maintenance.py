from __future__ import annotations

import json
import shutil
import tempfile
import zipfile
from pathlib import Path, PurePosixPath

from highland_mocks.store import reset_all

from .settings import HighlandSettings
from .workspace import WorkspacePaths

BACKUP_VERSION = 1
BACKUP_TARGETS = {
    "artifacts": "artifacts",
    "workflows": "workflows",
    "traces": "runs/events",
}


def reset_targets(settings: HighlandSettings) -> tuple[Path, ...]:
    workspace = WorkspacePaths.from_root(settings.workspace_dir)
    seed = settings.seed_dir.expanduser().resolve()
    runtime = settings.runtime_dir.expanduser().resolve()
    _validate_reset_roots(seed=seed, runtime=runtime, workspace=workspace.root)
    mock_files = tuple(runtime / f"{name}.json" for name in (
        "crm",
        "knowledge",
        "support",
        "observability",
        "communications",
        "projects",
    ))
    return (*mock_files, *workspace.state_directories)


def reset_local_state(settings: HighlandSettings) -> tuple[Path, ...]:
    targets = reset_targets(settings)
    reset_all(settings.seed_dir.expanduser().resolve(), settings.runtime_dir.expanduser().resolve())
    WorkspacePaths.from_root(settings.workspace_dir).reset()
    return targets


def export_learning_state(workspace_root: Path, destination: Path) -> Path:
    workspace = WorkspacePaths.from_root(workspace_root)
    destination = destination.expanduser().resolve()
    if destination.is_relative_to(workspace.root):
        raise ValueError("backup destination must be outside the Highland workspace")
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.with_suffix(destination.suffix + ".tmp")
    manifest = {
        "format": "highland-learning-state",
        "version": BACKUP_VERSION,
        "contents": sorted(BACKUP_TARGETS),
    }
    with zipfile.ZipFile(temporary, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("manifest.json", json.dumps(manifest, indent=2) + "\n")
        for label, relative in BACKUP_TARGETS.items():
            source = workspace.root / relative
            if not source.exists():
                continue
            for path in sorted(item for item in source.rglob("*") if item.is_file()):
                archive.write(path, PurePosixPath(label) / path.relative_to(source))
    temporary.replace(destination)
    return destination


def import_learning_state(
    workspace_root: Path,
    source: Path,
    *,
    replace: bool = False,
) -> tuple[Path, ...]:
    workspace = WorkspacePaths.from_root(workspace_root)
    source = source.expanduser().resolve()
    targets = {
        label: workspace.root / relative for label, relative in BACKUP_TARGETS.items()
    }
    occupied = [target for target in targets.values() if target.exists() and any(target.iterdir())]
    if occupied and not replace:
        names = ", ".join(str(path) for path in occupied)
        raise FileExistsError(f"backup targets are not empty; pass --replace: {names}")

    workspace.root.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(source) as archive:
        _validate_archive(archive)
        manifest = json.loads(archive.read("manifest.json"))
        if manifest.get("format") != "highland-learning-state":
            raise ValueError("not a Highland learning-state backup")
        if manifest.get("version") != BACKUP_VERSION:
            raise ValueError(f"unsupported backup version: {manifest.get('version')}")
        with tempfile.TemporaryDirectory(dir=workspace.root, prefix=".import-") as temporary:
            staging = Path(temporary)
            archive.extractall(staging)
            for label, target in targets.items():
                prepared = staging / label
                if target.exists():
                    shutil.rmtree(target)
                if prepared.exists():
                    shutil.copytree(prepared, target)
                else:
                    target.mkdir(parents=True)
    workspace.ensure()
    return tuple(targets.values())


def _validate_archive(archive: zipfile.ZipFile) -> None:
    allowed = {*BACKUP_TARGETS, "manifest.json"}
    for member in archive.infolist():
        path = PurePosixPath(member.filename)
        if path.is_absolute() or ".." in path.parts:
            raise ValueError(f"unsafe backup path: {member.filename}")
        if not path.parts or path.parts[0] not in allowed:
            raise ValueError(f"unexpected backup path: {member.filename}")
        if (member.external_attr >> 16) & 0o170000 == 0o120000:
            raise ValueError(f"backup contains a symbolic link: {member.filename}")


def _validate_reset_roots(*, seed: Path, runtime: Path, workspace: Path) -> None:
    broad = {Path(workspace.anchor), Path.home().resolve(), Path(__file__).resolve().parents[2]}
    if workspace in broad:
        raise ValueError(f"refusing broad workspace reset target: {workspace}")
    if workspace == seed or seed.is_relative_to(workspace):
        raise ValueError(f"workspace reset target contains checked-in seed data: {workspace}")
    if runtime == seed:
        raise ValueError("mock runtime directory must differ from seed directory")
