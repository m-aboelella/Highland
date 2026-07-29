from __future__ import annotations

import os
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field


def _now() -> datetime:
    return datetime.now(UTC)


class ConversationModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class SourceReference(ConversationModel):
    chunk_id: str
    source_id: str
    source_url: str
    source_system: str | None = None
    source_type: str | None = None
    title: str | None = None
    passage: str | None = None
    section: str | None = None
    updated_at: datetime | None = None
    customer_id: str | None = None
    score: float | None = None
    refreshed_through_mcp: bool = False


class ConversationMessage(ConversationModel):
    id: str = Field(default_factory=lambda: f"msg_{uuid4().hex}")
    role: str
    content: str
    created_at: datetime = Field(default_factory=_now)
    run_id: str | None = None
    sources: list[SourceReference] = Field(default_factory=list)


class Conversation(ConversationModel):
    version: int = 1
    id: str = Field(default_factory=lambda: f"con_{uuid4().hex}")
    title: str = "New conversation"
    created_at: datetime = Field(default_factory=_now)
    updated_at: datetime = Field(default_factory=_now)
    messages: list[ConversationMessage] = Field(default_factory=list)
    active_run_id: str | None = None


class ConversationStore:
    """Atomic, single-workspace conversation persistence."""

    def __init__(self, directory: Path) -> None:
        self.directory = directory

    def create(self, title: str | None = None) -> Conversation:
        conversation = Conversation(title=(title or "New conversation").strip())
        self.save(conversation)
        return conversation

    def list(self) -> list[Conversation]:
        if not self.directory.exists():
            return []
        conversations: list[Conversation] = []
        for path in self.directory.glob("con_*.json"):
            try:
                conversations.append(Conversation.model_validate_json(path.read_text("utf-8")))
            except (OSError, ValueError):
                continue
        return sorted(conversations, key=lambda item: item.updated_at, reverse=True)

    def get(self, conversation_id: str) -> Conversation:
        return Conversation.model_validate_json(self._path(conversation_id).read_text("utf-8"))

    def save(self, conversation: Conversation) -> None:
        self.directory.mkdir(parents=True, exist_ok=True)
        target = self._path(conversation.id)
        temporary = target.with_suffix(".tmp")
        temporary.write_text(conversation.model_dump_json(indent=2), encoding="utf-8")
        os.replace(temporary, target)

    def rename(self, conversation_id: str, title: str) -> Conversation:
        conversation = self.get(conversation_id)
        conversation.title = title.strip()
        conversation.updated_at = _now()
        self.save(conversation)
        return conversation

    def delete(self, conversation_id: str) -> None:
        self._path(conversation_id).unlink()

    def append_message(
        self,
        conversation_id: str,
        *,
        role: str,
        content: str,
        run_id: str | None = None,
        sources: list[SourceReference] | None = None,
    ) -> ConversationMessage:
        conversation = self.get(conversation_id)
        message = ConversationMessage(
            role=role,
            content=content,
            run_id=run_id,
            sources=sources or [],
        )
        conversation.messages.append(message)
        conversation.updated_at = _now()
        if run_id:
            conversation.active_run_id = run_id
        self.save(conversation)
        return message

    def context(self, conversation_id: str, *, max_chars: int) -> list[ConversationMessage]:
        selected: list[ConversationMessage] = []
        used = 0
        for message in reversed(self.get(conversation_id).messages):
            size = len(message.model_dump_json())
            if selected and used + size > max_chars:
                break
            selected.append(message)
            used += size
        return list(reversed(selected))

    def _path(self, conversation_id: str) -> Path:
        if not conversation_id.startswith("con_") or any(
            character not in "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_"
            for character in conversation_id
        ):
            raise FileNotFoundError(conversation_id)
        return self.directory / f"{conversation_id}.json"
