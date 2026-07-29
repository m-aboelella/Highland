from __future__ import annotations

import json
import os
from datetime import UTC, datetime
from pathlib import Path


class RunCancellationStore:
    def __init__(self, directory: Path) -> None:
        self.directory = directory

    def cancel(self, run_id: str, *, reason: str | None = None) -> dict[str, str | None]:
        payload = {
            "run_id": run_id,
            "cancelled_at": datetime.now(UTC).isoformat(),
            "reason": reason,
        }
        self.directory.mkdir(parents=True, exist_ok=True)
        target = self.directory / f"{run_id}.json"
        temporary = target.with_suffix(".tmp")
        temporary.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        os.replace(temporary, target)
        return payload

    def is_cancelled(self, run_id: str) -> bool:
        return (self.directory / f"{run_id}.json").exists()
