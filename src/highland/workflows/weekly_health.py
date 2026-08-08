from __future__ import annotations

import json
import os
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from highland.artifacts import Artifact, ArtifactCitation, ArtifactRepository, ArtifactType
from highland.models.contracts import (
    ChatModel,
    ChatRequest,
    Message,
    MessageRole,
    ModelCapabilities,
    Usage,
)
from highland.runtime.events import RunEventStore
from highland.runtime.policy import RunScope, ToolRegistry


class HealthModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class HealthClassification(HealthModel):
    classification: Literal["healthy", "watch", "at_risk"]
    rationale: str = Field(min_length=1)
    evidence_ids: list[str] = Field(default_factory=list)


class AccountHealth(HealthModel):
    customer_id: str
    customer_name: str
    classification: str
    rationale: str
    evidence_ids: list[str]
    usage: Usage


class WeeklyHealthRun(HealthModel):
    run_id: str
    workflow_id: str = "wf_weekly_customer_health"
    workflow_version: int
    accounts: list[AccountHealth]
    usage: Usage
    artifact_id: str
    started_at: datetime
    completed_at: datetime


class WeeklyHealthRunRepository:
    def __init__(self, directory: Path) -> None:
        self.directory = directory

    def save(self, run: WeeklyHealthRun) -> None:
        self.directory.mkdir(parents=True, exist_ok=True)
        target = self.directory / f"{run.run_id}.weekly-health.json"
        temporary = target.with_suffix(".tmp")
        temporary.write_text(run.model_dump_json(indent=2), encoding="utf-8")
        os.replace(temporary, target)


class WeeklyCustomerHealthRunner:
    """Acceptance workflow: canonical per-customer tools, then model classification."""

    signal_tools = (
        "crm__get_customer",
        "observability__get_deployment",
        "support__list_customer_tickets",
        "observability__list_incidents",
        "projects__list_project_issues",
    )

    def __init__(
        self,
        *,
        model: ChatModel,
        tools: ToolRegistry | Any,
        artifacts: ArtifactRepository,
        runs: WeeklyHealthRunRepository,
        events: RunEventStore | None = None,
    ) -> None:
        self.model = model
        self.tools = tools
        self.artifacts = artifacts
        self.runs = runs
        self.events = events

    async def run(
        self, *, run_id: str, workflow_version: int, scheduled: bool = False
    ) -> WeeklyHealthRun:
        started_at = datetime.now(UTC)
        if self.events:
            self.events.append(
                run_id,
                "run_started",
                {
                    "workflow_id": "wf_weekly_customer_health",
                    "workflow_version": workflow_version,
                    "scheduled": scheduled,
                },
            )
        customers_result = await self._tool(
            "crm__list_customers",
            {"status": "active"},
            run_id=run_id,
            step="customers",
            scope=RunScope(),
        )
        customers = _items(customers_result)
        active_enterprise = [
            customer
            for customer in customers
            if customer.get("status") == "active"
            and str(customer.get("tier", "")).lower() == "enterprise"
        ]
        seen: set[str] = set()
        accounts: list[AccountHealth] = []
        citations: list[ArtifactCitation] = []
        totals = Usage(input_tokens=0, output_tokens=0)
        for customer in active_enterprise:
            customer_id = str(customer["id"])
            if customer_id in seen:
                raise ValueError(f"duplicate active enterprise customer {customer_id}")
            seen.add(customer_id)
            scope = RunScope(allowed_customers=frozenset({customer_id}))
            evidence: dict[str, Any] = {}
            for tool_index, tool in enumerate(self.signal_tools, start=1):
                evidence_id = f"{customer_id}:S{tool_index}"
                evidence[evidence_id] = await self._tool(
                    tool,
                    {"customer_id": customer_id},
                    run_id=run_id,
                    step=f"{customer_id}:{tool_index}",
                    scope=scope,
                )
                citations.append(
                    ArtifactCitation(
                        id=evidence_id,
                        label=evidence_id,
                        source_id=f"{tool}:{customer_id}",
                        source_url=f"mock://{tool}/{customer_id}",
                        title=f"{tool} for {customer.get('name', customer_id)}",
                        passage=json.dumps(evidence[evidence_id], sort_keys=True)[:2000],
                        source_system=tool.partition("__")[0],
                    )
                )
            response = await self.model.chat(
                ChatRequest(
                    messages=[
                        Message(
                            role=MessageRole.SYSTEM,
                            content=(
                                "Classify this one enterprise account as healthy, watch, or "
                                "at_risk. Use only its supplied canonical evidence. Keep unknown "
                                "signals explicit and cite evidence IDs for every non-healthy "
                                "classification."
                            ),
                        ),
                        Message(
                            role=MessageRole.USER,
                            content=(
                                f"Customer: {customer_id}\n"
                                f"Scheduled run: {scheduled}\n"
                                f"Evidence:\n{json.dumps(evidence, sort_keys=True)}"
                            ),
                        ),
                    ],
                    response_schema=HealthClassification.model_json_schema(),
                    required_capabilities=ModelCapabilities(structured_output=True),
                    logical_call_id=f"{run_id}:health:{customer_id}",
                )
            )
            if self.events:
                self.events.append(
                    run_id,
                    "model_call",
                    {
                        "node_id": f"classify:{customer_id}",
                        "customer_id": customer_id,
                        "usage": response.usage.model_dump(mode="json"),
                    },
                )
            try:
                classification = HealthClassification.model_validate(
                    response.structured_output
                )
            except ValidationError as error:
                raise ValueError(
                    f"invalid health classification for {customer_id}: {error}"
                ) from error
            allowed_evidence = set(evidence)
            if not set(classification.evidence_ids) <= allowed_evidence:
                raise ValueError(f"cross-customer evidence returned for {customer_id}")
            if (
                classification.classification != "healthy"
                and not classification.evidence_ids
            ):
                raise ValueError(f"non-healthy account {customer_id} requires evidence")
            accounts.append(
                AccountHealth(
                    customer_id=customer_id,
                    customer_name=str(customer.get("name", customer_id)),
                    classification=classification.classification,
                    rationale=classification.rationale,
                    evidence_ids=classification.evidence_ids,
                    usage=response.usage,
                )
            )
            totals = totals + response.usage
        artifact = self._artifact(run_id, accounts, citations)
        result = WeeklyHealthRun(
            run_id=run_id,
            workflow_version=workflow_version,
            accounts=accounts,
            usage=totals,
            artifact_id=artifact.id,
            started_at=started_at,
            completed_at=datetime.now(UTC),
        )
        self.runs.save(result)
        if self.events:
            self.events.append(
                run_id,
                "run_completed",
                {
                    "workflow_id": result.workflow_id,
                    "workflow_version": result.workflow_version,
                    "accounts": [account.customer_id for account in result.accounts],
                    "artifact_id": result.artifact_id,
                },
            )
        return result

    async def _tool(
        self,
        name: str,
        arguments: dict[str, Any],
        *,
        run_id: str,
        step: str,
        scope: RunScope,
    ) -> Any:
        call = self.tools.validate(
            name,
            arguments,
            run_id=run_id,
            logical_step_id=step,
            scope=scope,
        )
        if self.events:
            self.events.append(
                run_id,
                "tool_call",
                {
                    "node_id": step,
                    "tool_call": {
                        "name": call.qualified_name,
                        "arguments": call.arguments,
                    },
                    "mode": call.policy.mode.value,
                    "customer_scope": sorted(scope.allowed_customers),
                },
            )
        result = await self.tools.execute(call)
        if self.events:
            self.events.append(
                run_id,
                "tool_result",
                {
                    "node_id": step,
                    "tool": call.qualified_name,
                    "content": result.content,
                    "structured_content": result.structured_content,
                    "is_error": result.is_error,
                },
            )
        if result.is_error:
            raise RuntimeError(result.content)
        if result.structured_content is not None:
            return result.structured_content
        try:
            return json.loads(result.content)
        except json.JSONDecodeError:
            return {"content": result.content}

    def _artifact(
        self,
        run_id: str,
        accounts: list[AccountHealth],
        citations: list[ArtifactCitation],
    ) -> Artifact:
        rows = []
        used: set[str] = set()
        for account in accounts:
            labels = " ".join(f"[{item}]" for item in account.evidence_ids)
            used.update(account.evidence_ids)
            rows.append(
                f"| {account.customer_name} | {account.classification} | "
                f"{account.rationale} {labels} |"
            )
        content = "\n".join(
            [
                "# Weekly customer health",
                "",
                "| Customer | Health | Evidence-backed rationale |",
                "| --- | --- | --- |",
                *rows,
            ]
        )
        return self.artifacts.create(
            title="Weekly customer health",
            artifact_type=ArtifactType.EXECUTIVE_SUMMARY,
            content=content,
            conversation_id="workflow:weekly-customer-health",
            run_id=run_id,
            citations=[citation for citation in citations if citation.id in used],
        )


def _items(value: Any) -> list[dict[str, Any]]:
    if not isinstance(value, dict):
        raise TypeError("customer list tool must return an object")
    items = value.get("items", value.get("customers"))
    if not isinstance(items, list) or not all(isinstance(item, dict) for item in items):
        raise TypeError("customer list tool did not return records")
    return items
