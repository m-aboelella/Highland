from pathlib import Path
from typing import ClassVar

import pytest

from highland.artifacts import ArtifactRepository
from highland.models.contracts import (
    ChatResponse,
    FinishReason,
    Message,
    MessageRole,
    Usage,
)
from highland.models.scripted import ScriptedChatModel, simulated_metadata
from highland.runtime.mcp import NormalizedToolResult
from highland.runtime.policy import ToolMode, ToolPolicy, ValidatedToolCall
from highland.workflows.weekly_health import (
    WeeklyCustomerHealthRunner,
    WeeklyHealthRunRepository,
)


class HealthTools:
    customers: ClassVar[list[dict[str, str]]] = [
        {"id": "cus_northwind", "name": "Northwind", "status": "active", "tier": "enterprise"},
        {"id": "cus_alpine", "name": "Alpine", "status": "active", "tier": "enterprise"},
        {"id": "cus_lumon", "name": "Lumon", "status": "active", "tier": "enterprise"},
        {"id": "cus_small", "name": "Small", "status": "active", "tier": "growth"},
    ]

    def __init__(self) -> None:
        self.calls: list[tuple[str, dict[str, object], frozenset[str]]] = []

    def validate(self, name, arguments, *, scope, **_):
        customer_id = arguments.get("customer_id")
        if customer_id and scope.allowed_customers != frozenset({customer_id}):
            raise AssertionError("customer evidence escaped loop scope")
        self.calls.append((name, arguments, scope.allowed_customers))
        return ValidatedToolCall(
            qualified_name=name,
            arguments=arguments,
            policy=ToolPolicy(mode=ToolMode.READ),
            idempotency_key=None,
        )

    async def execute(self, call):
        if call.qualified_name == "crm__list_customers":
            payload = {"items": self.customers}
        else:
            payload = {
                "items": [
                    {
                        "customer_id": call.arguments["customer_id"],
                        "signal": call.qualified_name,
                    }
                ]
            }
        return NormalizedToolResult(
            connector=call.qualified_name.partition("__")[0],
            tool=call.qualified_name,
            content="",
            structured_content=payload,
        )


def classification(label: str, customer_id: str, evidence: list[str]) -> ChatResponse:
    return ChatResponse(
        message=Message(role=MessageRole.ASSISTANT),
        finish_reason=FinishReason.COMPLETE,
        usage=Usage(input_tokens=10, output_tokens=5),
        metadata=simulated_metadata("weekly-health"),
        structured_output={
            "classification": label,
            "rationale": f"Evidence-backed {label} assessment for {customer_id}.",
            "evidence_ids": evidence,
        },
    )


@pytest.mark.asyncio
async def test_weekly_health_includes_each_enterprise_account_once_with_isolated_usage(
    tmp_path: Path,
) -> None:
    model = ScriptedChatModel(
        [
            classification("watch", "cus_northwind", ["cus_northwind:S2"]),
            classification("healthy", "cus_alpine", []),
            classification("at_risk", "cus_lumon", ["cus_lumon:S3", "cus_lumon:S4"]),
        ],
        model="weekly-health",
    )
    tools = HealthTools()
    artifacts = ArtifactRepository(tmp_path / "artifacts")
    result = await WeeklyCustomerHealthRunner(
        model=model,
        tools=tools,
        artifacts=artifacts,
        runs=WeeklyHealthRunRepository(tmp_path / "runs"),
    ).run(run_id="wrun_weekly", workflow_version=1, scheduled=True)

    assert [account.customer_id for account in result.accounts] == [
        "cus_northwind",
        "cus_alpine",
        "cus_lumon",
    ]
    assert [account.classification for account in result.accounts] == [
        "watch",
        "healthy",
        "at_risk",
    ]
    assert all(account.usage.total_tokens == 15 for account in result.accounts)
    assert result.usage.total_tokens == 45
    assert len(model.requests) == 3
    for customer_id, request in zip(
        ["cus_northwind", "cus_alpine", "cus_lumon"], model.requests, strict=True
    ):
        assert customer_id in request.messages[-1].content
        assert all(
            other not in request.messages[-1].content
            for other in {"cus_northwind", "cus_alpine", "cus_lumon"} - {customer_id}
        )
    artifact = artifacts.get(result.artifact_id)
    assert "| Northwind | watch |" in artifact.content
    assert "| Alpine | healthy |" in artifact.content
    assert "| Lumon | at_risk |" in artifact.content
    assert {citation.id for citation in artifact.citations} == {
        "cus_northwind:S2",
        "cus_lumon:S3",
        "cus_lumon:S4",
    }
