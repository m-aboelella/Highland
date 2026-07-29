from __future__ import annotations

from pathlib import Path

import pytest

from highland.runtime.approvals import ApprovalService, ApprovalStatus, ApprovalStore
from highland.runtime.mcp import NormalizedToolResult
from highland.runtime.policy import ToolMode, ToolPolicy, ValidatedToolCall


class Tools:
    def __init__(self) -> None:
        self.calls = 0

    async def execute_approved(self, call):
        self.calls += 1
        return NormalizedToolResult(
            connector="support",
            tool=call.qualified_name,
            content='{"ticket_id":"tkt_new"}',
        )


def pending(store: ApprovalStore):
    return store.create(
        run_id="run-1",
        tool_call_id="write-1",
        call=ValidatedToolCall(
            qualified_name="support__create_ticket",
            arguments={
                "customer_id": "cus_northwind",
                "title": "Investigate latency",
                "description": "Observed latency regression",
                "idempotency_key": "stable-key",
            },
            policy=ToolPolicy(
                mode=ToolMode.WRITE,
                approval_required=True,
                idempotent_with_key=True,
                customer_scoped=True,
            ),
            idempotency_key="stable-key",
        ),
        reason="Creates an external support record",
    )


@pytest.mark.asyncio
async def test_restart_preserves_approval_and_resume_is_idempotent(tmp_path: Path) -> None:
    request = pending(ApprovalStore(tmp_path))
    restarted = ApprovalStore(tmp_path)
    assert restarted.get(request.id).status is ApprovalStatus.PENDING
    restarted.decide(request.id, approve=True, reason="Proceed")
    tools = Tools()
    service = ApprovalService(restarted, tools)  # type: ignore[arg-type]
    first = await service.resume(request.id)
    second = await service.resume(request.id)
    assert first.content == second.content
    assert tools.calls == 1
    assert restarted.get(request.id).status is ApprovalStatus.COMPLETED


@pytest.mark.asyncio
async def test_rejection_never_executes_write(tmp_path: Path) -> None:
    store = ApprovalStore(tmp_path)
    request = pending(store)
    store.decide(request.id, approve=False, reason="Need more evidence")
    tools = Tools()
    result = await ApprovalService(store, tools).resume(request.id)  # type: ignore[arg-type]
    assert result.is_error
    assert result.error_type == "approval_rejected"
    assert tools.calls == 0


def test_repeated_decision_is_idempotent_and_conflict_is_rejected(tmp_path: Path) -> None:
    store = ApprovalStore(tmp_path)
    request = pending(store)
    first = store.decide(request.id, approve=True)
    second = store.decide(request.id, approve=True)
    assert first == second
    with pytest.raises(ValueError, match="already approved"):
        store.decide(request.id, approve=False)
