from __future__ import annotations

from pathlib import Path

import pytest

from highland.models.contracts import ChatRequest, Message, MessageRole, ModelError
from highland.models.scripted import ScriptedChatModel, ScriptedFailure
from highland.runtime.events import EventType, RunEventStore
from highland.runtime.mcp import MCPGateway
from highland.runtime.reliability import (
    FailureDisposition,
    TraceReplayService,
    failure_disposition,
)


@pytest.mark.asyncio
async def test_connector_unavailable_is_isolated() -> None:
    gateway = MCPGateway({"missing": ("executable-that-does-not-exist",)})
    await gateway.start()
    assert "missing" in gateway.failures
    result = await gateway.call("missing__search", {})
    assert result.is_error
    assert result.error_type == "unknown_tool"


@pytest.mark.parametrize(
    ("failure", "expected"),
    [
        ("http_429", FailureDisposition.RETRY),
        ("http_500", FailureDisposition.RETRY),
        ("http_503", FailureDisposition.RETRY),
        ("latency_timeout", FailureDisposition.RETRY),
        ("model_rate_limit", FailureDisposition.RETRY),
        ("malformed_model_output", FailureDisposition.TERMINAL),
        ("budget_stop", FailureDisposition.TERMINAL),
        ("restart_during_approval", FailureDisposition.RESUMABLE),
    ],
)
def test_failure_matrix_has_an_explicit_outcome(
    failure: str, expected: FailureDisposition
) -> None:
    assert failure_disposition(failure) is expected


def test_write_retry_requires_idempotency_key() -> None:
    assert (
        failure_disposition("http_503", write=True, idempotency_key=None)
        is FailureDisposition.TERMINAL
    )
    assert (
        failure_disposition("http_503", write=True, idempotency_key="stable")
        is FailureDisposition.RETRY
    )


@pytest.mark.asyncio
async def test_malformed_and_rate_limited_model_outputs_are_explicit() -> None:
    request = ChatRequest(messages=[Message(role=MessageRole.USER, content="test")])
    malformed = ScriptedChatModel([{"unexpected": True}])
    with pytest.raises(ModelError, match="malformed"):
        await malformed.chat(request)
    limited = ScriptedChatModel([ScriptedFailure.rate_limit()])
    with pytest.raises(ModelError) as captured:
        await limited.chat(request)
    assert captured.value.code == "rate_limit"
    assert captured.value.retryable


def test_corrupted_final_log_line_is_ignored(tmp_path: Path) -> None:
    store = RunEventStore(tmp_path)
    store.append("old", EventType.RUN_STARTED, {})
    with (tmp_path / "old.events.jsonl").open("a", encoding="utf-8") as stream:
        stream.write('{"partial":')
    events = TraceReplayService(store).replay("old")
    assert [event.type for event in events] == [EventType.RUN_STARTED]


@pytest.mark.asyncio
async def test_replay_is_read_only_and_rerun_is_fresh_and_linked(tmp_path: Path) -> None:
    store = RunEventStore(tmp_path)
    store.append("old", EventType.RUN_STARTED, {"secret": "not persisted"})
    service = TraceReplayService(store)
    path = tmp_path / "old.events.jsonl"
    before = path.read_bytes()
    assert service.replay("old")
    assert path.read_bytes() == before
    executed: list[str] = []

    async def execute(run_id: str) -> str:
        executed.append(run_id)
        return "done"

    run_id, result = await service.rerun("old", execute, new_run_id="new")
    assert (run_id, result) == ("new", "done")
    assert executed == ["new"]
    assert store.replay("new")[0].payload == {"rerun_of": "old", "execution": "fresh"}
