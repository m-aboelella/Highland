from __future__ import annotations

import os
import re
from pathlib import Path

from .schema import WorkflowDefinition, WorkflowVersion, utc_now


class WorkflowRepository:
    """Atomic editable drafts and immutable, human-readable published versions."""

    _valid_id = re.compile(r"^wf_[A-Za-z0-9_-]+$")

    def __init__(self, directory: Path) -> None:
        self.directory = directory

    def save_draft(self, definition: WorkflowDefinition) -> WorkflowDefinition:
        directory = self._directory(definition.id)
        directory.mkdir(parents=True, exist_ok=True)
        current = self.get_draft(definition.id) if (directory / "draft.json").exists() else None
        updated = definition.model_copy(
            update={
                "created_at": current.created_at if current else definition.created_at,
                "updated_at": utc_now(),
            }
        )
        self._atomic_write(directory / "draft.json", updated.model_dump_json(indent=2))
        return updated

    def get_draft(self, workflow_id: str) -> WorkflowDefinition:
        return WorkflowDefinition.model_validate_json(
            (self._directory(workflow_id) / "draft.json").read_text("utf-8")
        )

    def publish(
        self, workflow_id: str, *, planner: dict[str, object] | None = None
    ) -> WorkflowVersion:
        definition = self.get_draft(workflow_id)
        versions = self.list_versions(workflow_id)
        if versions and self._publishable_content(versions[-1].definition) == (
            self._publishable_content(definition)
        ):
            return versions[-1]
        published = WorkflowVersion(
            workflow_id=workflow_id,
            version=(versions[-1].version + 1 if versions else 1),
            definition=definition,
            planner=planner,
        )
        path = self._directory(workflow_id) / "versions" / f"{published.version}.json"
        if path.exists():
            raise FileExistsError(f"workflow version {published.version} already exists")
        path.parent.mkdir(parents=True, exist_ok=True)
        self._atomic_write(path, published.model_dump_json(indent=2))
        return published

    def get_version(self, workflow_id: str, version: int) -> WorkflowVersion:
        return WorkflowVersion.model_validate_json(
            (self._directory(workflow_id) / "versions" / f"{version}.json").read_text("utf-8")
        )

    def list_versions(self, workflow_id: str) -> list[WorkflowVersion]:
        path = self._directory(workflow_id) / "versions"
        if not path.exists():
            return []
        return [
            WorkflowVersion.model_validate_json(item.read_text("utf-8"))
            for item in sorted(path.glob("*.json"), key=lambda item: int(item.stem))
        ]

    def list(self) -> list[WorkflowDefinition]:
        if not self.directory.exists():
            return []
        drafts = []
        for path in self.directory.glob("wf_*/draft.json"):
            try:
                drafts.append(WorkflowDefinition.model_validate_json(path.read_text("utf-8")))
            except (OSError, ValueError):
                continue
        return sorted(drafts, key=lambda workflow: workflow.updated_at, reverse=True)

    def _directory(self, workflow_id: str) -> Path:
        if not self._valid_id.fullmatch(workflow_id):
            raise FileNotFoundError(workflow_id)
        return self.directory / workflow_id

    @staticmethod
    def _publishable_content(definition: WorkflowDefinition) -> dict[str, object]:
        return definition.model_dump(
            mode="json",
            exclude={"created_at", "updated_at"},
        )

    @staticmethod
    def _atomic_write(target: Path, content: str) -> None:
        temporary = target.with_suffix(f"{target.suffix}.tmp")
        temporary.write_text(content, encoding="utf-8")
        os.replace(temporary, target)
