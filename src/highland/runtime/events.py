from __future__ import annotations

import json
import os
from datetime import UTC, datetime
from enum import StrEnum
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class EventType(StrEnum):
    RUN_STARTED = "run_started"
    RUN_COMPLETED = "run_completed"
    RUN_FAILED = "run_failed"
    RUN_CANCELLED = "run_cancelled"
    RETRIEVAL = "retrieval"
    MODEL_CALL = "model_call"
    MODEL_DELTA = "model_delta"
    TOOL_CALL = "tool_call"
    TOOL_RESULT = "tool_result"
    APPROVAL_REQUIRED = "approval_required"
    APPROVAL_DECISION = "approval_decision"
    CITATION = "citation"
    USAGE = "usage"
    ERROR = "error"
    FINAL = "final"


class RunEvent(BaseModel):
    model_config = ConfigDict(extra="forbid")

    version: int = 1
    id: int = Field(ge=1)
    run_id: str
    type: EventType
    timestamp: datetime
    payload: dict[str, Any] = Field(default_factory=dict)


class RunEventStore:
    def __init__(
        self,
        directory: Path,
        *,
        sensitive_fields: set[str] | None = None,
    ) -> None:
        self.directory = directory
        self.sensitive_fields = {
            field.lower()
            for field in (sensitive_fields or {"api_key", "authorization", "secret", "token"})
        }

    def append(self, run_id: str, event_type: EventType | str, payload: dict[str, Any]) -> RunEvent:
        events = self.replay(run_id)
        event = RunEvent(
            id=(events[-1].id + 1) if events else 1,
            run_id=run_id,
            type=EventType(event_type),
            timestamp=datetime.now(UTC),
            payload=self._redact(payload),
        )
        self.directory.mkdir(parents=True, exist_ok=True)
        with self._path(run_id).open("a", encoding="utf-8") as stream:
            stream.write(event.model_dump_json() + "\n")
            stream.flush()
            os.fsync(stream.fileno())
        return event

    def replay(self, run_id: str, *, after_id: int = 0) -> list[RunEvent]:
        path = self._path(run_id)
        if not path.exists():
            return []
        lines = path.read_text(encoding="utf-8").splitlines()
        events: list[RunEvent] = []
        for index, line in enumerate(lines):
            try:
                event = RunEvent.model_validate_json(line)
            except (ValueError, json.JSONDecodeError):
                if index == len(lines) - 1:
                    break
                raise
            if event.id > after_id:
                events.append(event)
        return events

    def summary(self, run_id: str) -> dict[str, Any]:
        events = self.replay(run_id)
        return self._summary(run_id, events)

    def list_summaries(self) -> list[dict[str, Any]]:
        if not self.directory.exists():
            return []
        summaries = []
        for path in self.directory.glob("*.events.jsonl"):
            run_id = path.name.removesuffix(".events.jsonl")
            events = self.replay(run_id)
            if events:
                summaries.append(self._summary(run_id, events))
        return sorted(
            summaries,
            key=lambda item: str(item["updated_at"]),
            reverse=True,
        )

    def _summary(self, run_id: str, events: list[RunEvent]) -> dict[str, Any]:
        final = next(
            (event for event in reversed(events) if event.type is EventType.FINAL),
            None,
        )
        started = next(
            (event for event in events if event.type is EventType.RUN_STARTED),
            None,
        )
        retrieval = next(
            (event for event in events if event.type is EventType.RETRIEVAL),
            None,
        )
        content = final.payload.get("content") if final else None
        return {
            "run_id": run_id,
            "event_count": len(events),
            "status": _status(events),
            "final": final.payload if final else None,
            "last_event_id": events[-1].id if events else 0,
            "started_at": events[0].timestamp.isoformat() if events else None,
            "updated_at": events[-1].timestamp.isoformat() if events else None,
            "conversation_id": started.payload.get("conversation_id") if started else None,
            "message_id": started.payload.get("message_id") if started else None,
            "prompt": retrieval.payload.get("query") if retrieval else None,
            "final_preview": content[:180] if isinstance(content, str) else None,
        }

    def sse(self, event: RunEvent) -> str:
        return (
            f"id: {event.id}\n"
            f"event: {event.type.value}\n"
            f"data: {event.model_dump_json()}\n\n"
        )

    def _path(self, run_id: str) -> Path:
        return self.directory / f"{run_id}.events.jsonl"

    def _redact(self, value: Any, *, key: str = "") -> Any:
        lowered = key.lower()
        if lowered in self.sensitive_fields or any(
            marker in lowered for marker in ("api_key", "authorization")
        ):
            return "[REDACTED]"
        if isinstance(value, dict):
            return {name: self._redact(item, key=name) for name, item in value.items()}
        if isinstance(value, list):
            return [self._redact(item, key=key) for item in value]
        return value


def _status(events: list[RunEvent]) -> str:
    types = {event.type for event in events}
    if EventType.RUN_CANCELLED in types:
        return "cancelled"
    if EventType.RUN_FAILED in types:
        return "failed"
    if EventType.RUN_COMPLETED in types or EventType.FINAL in types:
        return "completed"
    if EventType.APPROVAL_REQUIRED in types:
        return "paused"
    return "running" if events else "missing"
