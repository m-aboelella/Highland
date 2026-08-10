from __future__ import annotations

import os
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import uuid4

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.interval import IntervalTrigger
from pydantic import BaseModel, ConfigDict, Field

from .schema import WorkflowVersion


class ScheduleModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class WorkflowSchedule(ScheduleModel):
    schema_version: int = 1
    id: str = Field(default_factory=lambda: f"sch_{uuid4().hex}")
    workflow_id: str
    workflow_version: int = Field(ge=1)
    interval_seconds: int = Field(ge=60)
    enabled: bool = True
    next_run_at: datetime
    last_started_for: datetime | None = None


class WorkflowScheduleRepository:
    def __init__(self, directory: Path) -> None:
        self.directory = directory

    def create(
        self,
        published: WorkflowVersion,
        *,
        interval_seconds: int,
        start_at: datetime | None = None,
    ) -> WorkflowSchedule:
        if published.version < 1:
            raise ValueError("only published workflow versions can be scheduled")
        schedule = WorkflowSchedule(
            workflow_id=published.workflow_id,
            workflow_version=published.version,
            interval_seconds=interval_seconds,
            next_run_at=start_at or datetime.now(UTC) + timedelta(seconds=interval_seconds),
        )
        self.save(schedule)
        return schedule

    def save(self, schedule: WorkflowSchedule) -> None:
        self.directory.mkdir(parents=True, exist_ok=True)
        target = self.directory / f"{schedule.id}.json"
        temporary = target.with_suffix(".tmp")
        temporary.write_text(schedule.model_dump_json(indent=2), encoding="utf-8")
        os.replace(temporary, target)

    def get(self, schedule_id: str) -> WorkflowSchedule:
        return WorkflowSchedule.model_validate_json(
            (self.directory / f"{schedule_id}.json").read_text("utf-8")
        )

    def list(self) -> list[WorkflowSchedule]:
        if not self.directory.exists():
            return []
        return [
            WorkflowSchedule.model_validate_json(path.read_text("utf-8"))
            for path in sorted(self.directory.glob("sch_*.json"))
        ]

    def delete_for_workflow(self, workflow_id: str) -> int:
        deleted = 0
        for schedule in self.list():
            if schedule.workflow_id == workflow_id:
                (self.directory / f"{schedule.id}.json").unlink(missing_ok=True)
                deleted += 1
        return deleted

    def claim_due(
        self, schedule_id: str, *, now: datetime | None = None
    ) -> tuple[WorkflowSchedule, datetime] | None:
        now = now or datetime.now(UTC)
        schedule = self.get(schedule_id)
        if not schedule.enabled or schedule.next_run_at > now:
            return None
        intended = schedule.next_run_at
        if schedule.last_started_for == intended:
            return None
        next_run = intended
        while next_run <= now:
            next_run += timedelta(seconds=schedule.interval_seconds)
        claimed = schedule.model_copy(
            update={"last_started_for": intended, "next_run_at": next_run}
        )
        # Persist the claim before starting non-idempotent work.
        self.save(claimed)
        return claimed, intended


class LocalWorkflowScheduler:
    """In-process APScheduler adapter; schedule definitions remain the source of truth."""

    def __init__(
        self,
        repository: WorkflowScheduleRepository,
        run: Callable[[WorkflowSchedule, datetime], Awaitable[None]],
    ) -> None:
        self.repository = repository
        self.run = run
        self.scheduler = AsyncIOScheduler(timezone=UTC)

    def start(self) -> None:
        for schedule in self.repository.list():
            if schedule.enabled:
                self.scheduler.add_job(
                    self._tick,
                    IntervalTrigger(seconds=schedule.interval_seconds),
                    args=(schedule.id,),
                    id=schedule.id,
                    replace_existing=True,
                    next_run_time=schedule.next_run_at,
                    coalesce=True,
                    max_instances=1,
                )
        self.scheduler.start()

    async def _tick(self, schedule_id: str) -> None:
        claim = self.repository.claim_due(schedule_id)
        if claim:
            schedule, intended = claim
            await self.run(schedule, intended)

    def shutdown(self) -> None:
        if self.scheduler.running:
            self.scheduler.shutdown(wait=False)
