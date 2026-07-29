from __future__ import annotations

import asyncio
from pathlib import Path

from fastapi.testclient import TestClient

from highland.api import create_app
from highland.models.provider import build_model_provider
from highland.runtime.agent import AgentLoop, AgentProfile, RunRepository, RunStatus
from highland.runtime.cancellation import RunCancellationStore
from highland.runtime.events import RunEventStore
from highland.runtime.mcp import MCPGateway
from highland.runtime.policy import ToolRegistry
from highland.settings import HighlandSettings


def test_cancellation_is_durable_and_prevents_model_steps(tmp_path: Path) -> None:
    settings = HighlandSettings(workspace_dir=tmp_path, connector_commands={})
    cancellations = RunCancellationStore(tmp_path / "runs" / "cancellations")
    cancellations.cancel("run-cancelled")
    loop = AgentLoop(
        build_model_provider(settings).chat,
        ToolRegistry.from_file(MCPGateway({}), settings.tool_policy_config),
        AgentProfile.load(settings.agent_profile_config),
        RunRepository(tmp_path / "runs" / "state"),
        event_store=RunEventStore(tmp_path / "runs" / "events"),
        cancellations=cancellations,
    )

    outcome = asyncio.run(loop.run(run_id="run-cancelled", user_message="Do not start"))
    assert outcome.status is RunStatus.CANCELLED
    state = loop.repository.load("run-cancelled")
    assert [event["type"] for event in state["events"]] == ["run_started", "run_cancelled"]


def test_cancel_endpoint_and_sse_trace_are_redacted(tmp_path: Path) -> None:
    settings = HighlandSettings(workspace_dir=tmp_path, connector_commands={})
    events = RunEventStore(tmp_path / "runs" / "events")
    events.append("run-1", "tool_call", {"authorization": "secret", "arguments": {"id": 1}})
    with TestClient(create_app(settings)) as client:
        cancelled = client.post("/runs/run-1/cancel", json={"reason": "user request"})
        trace = client.get("/runs/run-1/trace").json()
        replay = client.get("/runs/run-1/events", headers={"Last-Event-ID": "1"}).text

    assert cancelled.status_code == 202
    assert trace[0]["payload"]["authorization"] == "[REDACTED]"
    assert "event: run_cancelled" in replay
    assert "event: tool_call" not in replay
