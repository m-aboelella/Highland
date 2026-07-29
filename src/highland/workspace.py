from __future__ import annotations

import shutil
from dataclasses import dataclass, fields
from pathlib import Path


@dataclass(frozen=True, slots=True)
class WorkspacePaths:
    root: Path
    indexes: Path
    conversations: Path
    runs: Path
    artifacts: Path
    workflows: Path
    synchronization: Path

    @classmethod
    def from_root(cls, root: Path) -> WorkspacePaths:
        root = root.expanduser().resolve()
        return cls(
            root=root,
            indexes=root / "indexes",
            conversations=root / "conversations",
            runs=root / "runs",
            artifacts=root / "artifacts",
            workflows=root / "workflows",
            synchronization=root / "synchronization",
        )

    @property
    def state_directories(self) -> tuple[Path, ...]:
        return tuple(getattr(self, field.name) for field in fields(self) if field.name != "root")

    def ensure(self) -> None:
        for directory in self.state_directories:
            directory.mkdir(parents=True, exist_ok=True)

    def reset(self) -> None:
        """Remove only Highland platform state, then recreate its layout."""
        for directory in self.state_directories:
            if directory.exists():
                shutil.rmtree(directory)
        self.ensure()
