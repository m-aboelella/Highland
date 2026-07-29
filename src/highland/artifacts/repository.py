from __future__ import annotations

import os
import re
import shutil
from datetime import UTC, datetime
from enum import StrEnum
from pathlib import Path
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field


def _now() -> datetime:
    return datetime.now(UTC)


class ArtifactModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ArtifactType(StrEnum):
    BRIEFING = "briefing"
    INCIDENT_REPORT = "incident_report"
    ROLLOUT_PLAN = "rollout_plan"
    EXECUTIVE_SUMMARY = "executive_summary"
    COMPARISON = "comparison"
    PROPOSAL = "proposal"


class ArtifactCitation(ArtifactModel):
    id: str
    label: str
    source_id: str
    source_url: str
    chunk_id: str | None = None
    title: str | None = None
    passage: str | None = None
    source_system: str | None = None
    updated_at: datetime | None = None


class ArtifactRevision(ArtifactModel):
    revision: int = Field(ge=1)
    content: str
    citations: list[ArtifactCitation] = Field(default_factory=list)
    created_at: datetime = Field(default_factory=_now)
    reason: str = "manual edit"


class Artifact(ArtifactModel):
    schema_version: int = 1
    id: str = Field(default_factory=lambda: f"art_{uuid4().hex}")
    title: str
    artifact_type: ArtifactType
    content: str
    citations: list[ArtifactCitation] = Field(default_factory=list)
    revision: int = 1
    conversation_id: str
    run_id: str
    message_id: str | None = None
    created_at: datetime = Field(default_factory=_now)
    updated_at: datetime = Field(default_factory=_now)


class StaleArtifactRevision(ValueError):
    """An update was based on an artifact revision that is no longer current."""


class ArtifactRepository:
    """Atomic Markdown and JSON artifact persistence with immutable revision snapshots."""

    _valid_id = re.compile(r"^art_[A-Za-z0-9_]+$")

    def __init__(self, directory: Path) -> None:
        self.directory = directory

    def create(
        self,
        *,
        title: str,
        artifact_type: ArtifactType,
        content: str,
        conversation_id: str,
        run_id: str,
        message_id: str | None = None,
        citations: list[ArtifactCitation] | None = None,
    ) -> Artifact:
        artifact = Artifact(
            title=title.strip(),
            artifact_type=artifact_type,
            content=content,
            citations=citations or [],
            conversation_id=conversation_id,
            run_id=run_id,
            message_id=message_id,
        )
        self._write(artifact, reason="created")
        return artifact

    def list(self) -> list[Artifact]:
        if not self.directory.exists():
            return []
        artifacts: list[Artifact] = []
        for path in self.directory.glob("art_*/metadata.json"):
            try:
                artifacts.append(self._load(path.parent))
            except (OSError, ValueError):
                continue
        return sorted(artifacts, key=lambda item: item.updated_at, reverse=True)

    def get(self, artifact_id: str) -> Artifact:
        return self._load(self._artifact_dir(artifact_id))

    def update(
        self,
        artifact_id: str,
        *,
        expected_revision: int,
        title: str | None = None,
        content: str | None = None,
        citations: list[ArtifactCitation] | None = None,
        reason: str = "manual edit",
    ) -> Artifact:
        artifact = self.get(artifact_id)
        if artifact.revision != expected_revision:
            raise StaleArtifactRevision(
                f"Artifact is at revision {artifact.revision}, not {expected_revision}"
            )
        updated = artifact.model_copy(
            update={
                "title": title.strip() if title is not None else artifact.title,
                "content": content if content is not None else artifact.content,
                "citations": citations if citations is not None else artifact.citations,
                "revision": artifact.revision + 1,
                "updated_at": _now(),
            }
        )
        self._write(updated, reason=reason)
        return updated

    def delete(self, artifact_id: str) -> None:
        directory = self._artifact_dir(artifact_id)
        if not directory.exists():
            raise FileNotFoundError(artifact_id)
        shutil.rmtree(directory)

    def revisions(self, artifact_id: str) -> list[ArtifactRevision]:
        revisions_dir = self._artifact_dir(artifact_id) / "revisions"
        if not revisions_dir.exists():
            raise FileNotFoundError(artifact_id)
        revisions = [
            ArtifactRevision.model_validate_json(path.read_text("utf-8"))
            for path in sorted(revisions_dir.glob("*.json"), key=lambda item: int(item.stem))
        ]
        return revisions

    def revision(self, artifact_id: str, revision: int) -> ArtifactRevision:
        return ArtifactRevision.model_validate_json(
            (self._artifact_dir(artifact_id) / "revisions" / f"{revision}.json").read_text("utf-8")
        )

    def _load(self, directory: Path) -> Artifact:
        metadata_path = directory / "metadata.json"
        metadata = Artifact.model_validate_json(metadata_path.read_text("utf-8"))
        return metadata.model_copy(update={"content": (directory / "content.md").read_text("utf-8")})

    def _write(self, artifact: Artifact, *, reason: str) -> None:
        directory = self._artifact_dir(artifact.id)
        revisions = directory / "revisions"
        revisions.mkdir(parents=True, exist_ok=True)
        metadata = artifact.model_copy(update={"content": ""})
        self._atomic_write(directory / "content.md", artifact.content)
        self._atomic_write(directory / "metadata.json", metadata.model_dump_json(indent=2))
        snapshot = ArtifactRevision(
            revision=artifact.revision,
            content=artifact.content,
            citations=artifact.citations,
            created_at=artifact.updated_at,
            reason=reason,
        )
        self._atomic_write(
            revisions / f"{artifact.revision}.json", snapshot.model_dump_json(indent=2)
        )
        self._atomic_write(revisions / f"{artifact.revision}.md", artifact.content)

    @staticmethod
    def _atomic_write(target: Path, content: str) -> None:
        temporary = target.with_suffix(f"{target.suffix}.tmp")
        temporary.write_text(content, encoding="utf-8")
        os.replace(temporary, target)

    def _artifact_dir(self, artifact_id: str) -> Path:
        if not self._valid_id.fullmatch(artifact_id):
            raise FileNotFoundError(artifact_id)
        return self.directory / artifact_id
