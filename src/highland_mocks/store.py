from __future__ import annotations

import json
import os
import shutil
import threading
from pathlib import Path
from typing import Any

from .constants import DEFAULT_RUNTIME_DIR, DEFAULT_SEED_DIR, SERVICES


class JsonStore:
    """Small durable store that makes the generated files behave like source systems."""

    def __init__(
        self,
        service: str,
        seed_dir: Path | None = None,
        runtime_dir: Path | None = None,
    ) -> None:
        if service not in SERVICES:
            raise ValueError(f"Unknown service: {service}")
        self.service = service
        self.seed_dir = seed_dir or Path(os.getenv("HIGHLAND_SEED_DIR", str(DEFAULT_SEED_DIR)))
        self.runtime_dir = runtime_dir or Path(
            os.getenv("HIGHLAND_RUNTIME_DIR", str(DEFAULT_RUNTIME_DIR))
        )
        self.seed_path = self.seed_dir / f"{service}.json"
        self.runtime_path = self.runtime_dir / f"{service}.json"
        self._lock = threading.RLock()
        self._bootstrap()

    def _bootstrap(self) -> None:
        if not self.seed_path.exists():
            raise FileNotFoundError(
                f"Seed data is missing at {self.seed_path}. Run `highland-mocks generate`."
            )
        self.runtime_dir.mkdir(parents=True, exist_ok=True)
        if not self.runtime_path.exists():
            shutil.copyfile(self.seed_path, self.runtime_path)

    def read(self) -> dict[str, Any]:
        with self._lock:
            return json.loads(self.runtime_path.read_text(encoding="utf-8"))

    def write(self, data: dict[str, Any]) -> None:
        with self._lock:
            temporary = self.runtime_path.with_suffix(".json.tmp")
            temporary.write_text(
                json.dumps(data, indent=2, sort_keys=False) + "\n",
                encoding="utf-8",
            )
            temporary.replace(self.runtime_path)

    def reset(self) -> None:
        with self._lock:
            self.runtime_dir.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(self.seed_path, self.runtime_path)


def reset_all(
    seed_dir: Path = DEFAULT_SEED_DIR,
    runtime_dir: Path = DEFAULT_RUNTIME_DIR,
) -> list[Path]:
    reset_paths = []
    for service in SERVICES:
        store = JsonStore(service, seed_dir=seed_dir, runtime_dir=runtime_dir)
        store.reset()
        reset_paths.append(store.runtime_path)
    return reset_paths
