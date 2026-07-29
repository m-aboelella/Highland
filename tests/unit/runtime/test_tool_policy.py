from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from highland.runtime.mcp import MCPTool, NormalizedToolResult
from highland.runtime.policy import RunScope, ToolRegistry, ToolRejected

POLICY = Path(__file__).parents[3] / "config" / "tool_policy.json"


class Gateway:
    def __init__(self) -> None:
        self.tools = [
            MCPTool(
                connector="support",
                source_name="create_ticket",
                qualified_name="support__create_ticket",
                description="Create ticket",
                input_schema={
                    "type": "object",
                    "required": ["customer_id", "title", "description", "idempotency_key"],
                    "properties": {
                        "customer_id": {"type": "string"},
                        "title": {"type": "string"},
                        "description": {"type": "string"},
                        "idempotency_key": {"type": "string"},
                    },
                    "additionalProperties": False,
                },
            ),
            MCPTool(
                connector="crm",
                source_name="get_customer",
                qualified_name="crm__get_customer",
                description="Get customer",
                input_schema={
                    "type": "object",
                    "required": ["customer_id"],
                    "properties": {"customer_id": {"type": "string"}},
                    "additionalProperties": False,
                },
            ),
        ]
        self.calls: list[tuple[str, dict[str, Any]]] = []

    async def call(self, name: str, arguments: dict[str, Any]) -> NormalizedToolResult:
        self.calls.append((name, arguments))
        return NormalizedToolResult(connector="crm", tool=name, content="ok")


def registry() -> tuple[ToolRegistry, Gateway]:
    gateway = Gateway()
    return ToolRegistry.from_file(gateway, POLICY), gateway  # type: ignore[arg-type]


def test_write_policy_cannot_be_bypassed_with_alias_and_reuses_key() -> None:
    tools, _ = registry()
    scope = RunScope(allowed_customers=frozenset({"cus_northwind"}))
    with pytest.raises(ToolRejected, match="Unknown"):
        tools.validate(
            "create_ticket",
            {"customer_id": "cus_northwind", "title": "T", "description": "D"},
            run_id="run-1",
            logical_step_id="step-1",
            scope=scope,
        )
    first = tools.validate(
        "support__create_ticket",
        {"customer_id": "cus_northwind", "title": "T", "description": "D"},
        run_id="run-1",
        logical_step_id="step-1",
        scope=scope,
    )
    retry = tools.validate(
        "support__create_ticket",
        {"customer_id": "cus_northwind", "title": "T", "description": "D"},
        run_id="run-1",
        logical_step_id="step-1",
        scope=scope,
    )
    assert first.policy.approval_required
    assert first.idempotency_key == retry.idempotency_key
    assert first.arguments["idempotency_key"] == first.idempotency_key


def test_invalid_arguments_and_customer_scope_are_bounded() -> None:
    tools, _ = registry()
    scope = RunScope(allowed_customers=frozenset({"cus_northwind"}))
    with pytest.raises(ToolRejected) as invalid:
        tools.validate(
            "crm__get_customer",
            {"customer_id": 42},
            run_id="run",
            logical_step_id="1",
            scope=scope,
        )
    assert invalid.value.code == "invalid_arguments"
    assert len(str(invalid.value)) < 1000
    with pytest.raises(ToolRejected) as forbidden:
        tools.validate(
            "crm__get_customer",
            {"customer_id": "cus_globex"},
            run_id="run",
            logical_step_id="2",
            scope=scope,
        )
    assert forbidden.value.code == "customer_scope"


@pytest.mark.asyncio
async def test_read_tool_executes_after_validation() -> None:
    tools, gateway = registry()
    call = tools.validate(
        "crm__get_customer",
        {"customer_id": "cus_northwind"},
        run_id="run",
        logical_step_id="1",
        scope=RunScope(allowed_customers=frozenset({"cus_northwind"})),
    )
    result = await tools.execute(call)
    assert not result.is_error
    assert gateway.calls == [("crm__get_customer", {"customer_id": "cus_northwind"})]
