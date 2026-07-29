from __future__ import annotations

import json
import os
import threading
from datetime import UTC, date, datetime
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field

from highland.models.contracts import Usage


class UsageEntry(BaseModel):
    model_config = ConfigDict(extra="forbid")

    timestamp: datetime
    run_id: str
    logical_call_id: str | None = None
    operation: str
    provider: str
    model: str
    request_id: str | None = None
    usage: Usage
    estimated_cost_usd: float | None = Field(default=None, ge=0)
    pricing_known: bool
    latency_ms: float | None = Field(default=None, ge=0)


class UsageSummary(BaseModel):
    calls: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    search_units: float = 0
    known_cost_usd: float = 0
    unknown_price_calls: int = 0


class CostLedger:
    def __init__(self, path: Path) -> None:
        self.path = path
        self._lock = threading.Lock()

    def append(self, entry: UsageEntry) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        encoded = entry.model_dump_json() + "\n"
        with self._lock, self.path.open("a", encoding="utf-8") as handle:
            handle.write(encoded)
            handle.flush()
            os.fsync(handle.fileno())

    def entries(self) -> list[UsageEntry]:
        if not self.path.exists():
            return []
        lines = self.path.read_text(encoding="utf-8").splitlines()
        entries: list[UsageEntry] = []
        for index, line in enumerate(lines):
            if not line.strip():
                continue
            try:
                entries.append(UsageEntry.model_validate_json(line))
            except (ValueError, json.JSONDecodeError):
                if index == len(lines) - 1:
                    break
                raise
        return entries

    def summarize(
        self,
        *,
        run_id: str | None = None,
        month: date | None = None,
    ) -> UsageSummary:
        selected = self.entries()
        if run_id is not None:
            selected = [entry for entry in selected if entry.run_id == run_id]
        if month is not None:
            selected = [
                entry
                for entry in selected
                if entry.timestamp.year == month.year and entry.timestamp.month == month.month
            ]
        return UsageSummary(
            calls=len(selected),
            input_tokens=sum(entry.usage.input_tokens or 0 for entry in selected),
            output_tokens=sum(entry.usage.output_tokens or 0 for entry in selected),
            search_units=sum(entry.usage.search_units or 0 for entry in selected),
            known_cost_usd=sum(entry.estimated_cost_usd or 0 for entry in selected),
            unknown_price_calls=sum(not entry.pricing_known for entry in selected),
        )

    def latest_run_id(self) -> str | None:
        entries = self.entries()
        return entries[-1].run_id if entries else None


def new_usage_entry(
    *,
    run_id: str,
    operation: str,
    provider: str,
    model: str,
    usage: Usage,
    estimated_cost_usd: float | None,
    logical_call_id: str | None = None,
    request_id: str | None = None,
    latency_ms: float | None = None,
) -> UsageEntry:
    return UsageEntry(
        timestamp=datetime.now(UTC),
        run_id=run_id,
        logical_call_id=logical_call_id,
        operation=operation,
        provider=provider,
        model=model,
        request_id=request_id,
        usage=usage,
        estimated_cost_usd=estimated_cost_usd,
        pricing_known=estimated_cost_usd is not None,
        latency_ms=latency_ms,
    )
