from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path

from .contracts import SyncManifest


def save_manifest(path: Path, manifest: SyncManifest) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            json.dump(manifest.model_dump(mode="json"), handle, indent=2, sort_keys=True)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def load_manifest(path: Path) -> SyncManifest | None:
    if not path.exists():
        return None
    return SyncManifest.model_validate_json(path.read_text(encoding="utf-8"))
