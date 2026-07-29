from __future__ import annotations

from collections.abc import Awaitable, Callable
from enum import StrEnum
from uuid import uuid4

from .events import EventType, RunEvent, RunEventStore


class FailureDisposition(StrEnum):
    RETRY = "retry"
    TERMINAL = "terminal"
    RESUMABLE = "resumable"


_TRANSIENT = {
    "connector_unavailable",
    "http_429",
    "http_500",
    "http_503",
    "latency_timeout",
    "model_rate_limit",
}
_RESUMABLE = {"restart_during_approval"}


def failure_disposition(
    failure: str,
    *,
    write: bool = False,
    idempotency_key: str | None = None,
) -> FailureDisposition:
    """Return the documented recovery action, failing closed for unknown errors."""
    if failure in _RESUMABLE:
        return FailureDisposition.RESUMABLE
    if failure in _TRANSIENT and (not write or idempotency_key):
        return FailureDisposition.RETRY
    return FailureDisposition.TERMINAL


class TraceReplayService:
    """Read existing traces or explicitly launch a linked fresh run."""

    def __init__(self, store: RunEventStore) -> None:
        self.store = store

    def replay(self, run_id: str, *, after_id: int = 0) -> list[RunEvent]:
        return self.store.replay(run_id, after_id=after_id)

    async def rerun(
        self,
        original_run_id: str,
        execute: Callable[[str], Awaitable[object]],
        *,
        new_run_id: str | None = None,
    ) -> tuple[str, object]:
        if not self.store.replay(original_run_id):
            raise ValueError(f"cannot re-run missing trace {original_run_id}")
        run_id = new_run_id or f"rerun-{uuid4().hex}"
        if run_id == original_run_id or self.store.replay(run_id):
            raise ValueError("re-run requires a new unused run ID")
        self.store.append(
            run_id,
            EventType.RUN_STARTED,
            {"rerun_of": original_run_id, "execution": "fresh"},
        )
        try:
            result = await execute(run_id)
        except Exception as error:
            self.store.append(
                run_id,
                EventType.RUN_FAILED,
                {"rerun_of": original_run_id, "error_type": type(error).__name__},
            )
            raise
        return run_id, result
