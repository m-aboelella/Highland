from __future__ import annotations

import json
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import ClassVar

import pytest

from highland.evaluation.scenarios import LiveScenarioEvaluator
from highland.models.contracts import (
    ChatResponse,
    Citation,
    FinishReason,
    Message,
    MessageRole,
    ResponseMetadata,
    ToolCall,
    Usage,
)
from highland.models.provider import ModelProvider
from highland.models.scripted import (
    DeterministicEmbeddingModel,
    DeterministicRerankModel,
    ScriptedChatModel,
)
from highland.retrieval.contracts import SourceDocument, chunk_document
from highland.retrieval.faiss_store import EmbeddingIndex
from highland.retrieval.ingestion import promote_snapshot
from highland.runtime.mcp import NormalizedToolResult
from highland.runtime.policy import ToolMode, ToolPolicy, ValidatedToolCall
from highland.services import ApplicationServices
from highland.settings import HighlandSettings
from highland.workflows import WeeklyCustomerHealthRunner, WeeklyHealthRunRepository


def _response(
    content: str = "",
    *,
    citations: list[Citation] | None = None,
    tool_calls: list[ToolCall] | None = None,
    structured: dict[str, object] | None = None,
) -> ChatResponse:
    return ChatResponse(
        message=Message(
            role=MessageRole.ASSISTANT,
            content=content,
            tool_calls=tool_calls or [],
        ),
        citations=citations or [],
        finish_reason=(FinishReason.TOOL_CALL if tool_calls else FinishReason.COMPLETE),
        usage=Usage(input_tokens=10, output_tokens=5),
        metadata=ResponseMetadata(provider="scripted", model="scenario-model", simulated=True),
        structured_output=structured,
    )


async def _services(tmp_path: Path, steps: list[ChatResponse], *, tools: bool = False):
    embeddings = DeterministicEmbeddingModel()
    provider = ModelProvider(
        chat=ScriptedChatModel(steps, model="scenario-model"),
        embeddings=embeddings,
        rerank=DeterministicRerankModel(),
    )
    commands = (
        {
            "support": (
                sys.executable,
                "-m",
                "highland_mocks.mcp_server",
                "support",
            )
        }
        if tools
        else {}
    )
    settings = HighlandSettings(
        workspace_dir=tmp_path / "workspace",
        connector_commands=commands,
        connector_timeout_seconds=3,
    )
    services = ApplicationServices.build(settings, model_provider=provider)
    document = SourceDocument(
        source_system="archive",
        source_id="e1",
        title="Supported fact",
        text="The fact is supported by exact production retrieval content.",
        source_type="runbook",
        visibility="company",
        updated_at=datetime(2026, 7, 29, tzinfo=UTC),
        source_url="http://archive/documents/e1",
    )
    chunks = chunk_document(document)
    vector_source = tmp_path / "vector-stage"
    await EmbeddingIndex(embeddings).build(chunks, vector_source)
    promote_snapshot(
        services.workspace.indexes / "search",
        chunks=chunks,
        documents=[document],
        started=document.updated_at,
        completed=document.updated_at,
        counts={"archive": 1},
        vector_source=vector_source,
    )
    return services, chunks[0].id


def _manifest(path: Path, *, approval: bool = False) -> None:
    path.write_text(
        json.dumps(
            {
                "id": "scenario_one",
                "prompt": "Explain the fact.",
                "allowed_customers": None,
                "required_tools": [],
                "expected_claims": [
                    {"claim": "The fact is supported.", "evidence": ["e1"]}
                ],
                "forbidden_behavior": ["Invent evidence."],
                "approval_checkpoint": (
                    {"before_tool": "create_ticket"} if approval else None
                ),
            }
        )
    )


@pytest.mark.asyncio
async def test_scenario_runs_discover_and_aggregates_trace_usage_and_scores(
    tmp_path: Path,
) -> None:
    manifest = tmp_path / "scenario.json"
    _manifest(manifest)
    # Build once to learn the deterministic chunk ID, then replace the provider script.
    services, chunk_id = await _services(tmp_path, [])
    services.provider.chat._steps.extend(  # type: ignore[attr-defined]
        [
            _response(
                "The fact is supported.",
                citations=[Citation(start=4, end=8, text="fact", source_ids=[chunk_id])],
            ),
            _response(
                "{}",
                structured={
                    "claim_scores": [1.0],
                    "behavior_score": 1.0,
                    "final_structure_score": 0.9,
                },
            ),
        ]
    )
    evaluation = await LiveScenarioEvaluator(
        services, reports_dir=tmp_path / "reports"
    ).evaluate(manifest)
    assert evaluation.passed
    assert evaluation.pass_rate == 1
    assert evaluation.total_usage.total_tokens == 30
    assert evaluation.mean_semantic_scores["expected_claims"] == 1
    assert evaluation.trace_ids[0].startswith("eval-scenario_one-")
    events = services.run_events.replay(evaluation.trace_ids[0])
    assert {event.type.value for event in events} >= {"retrieval", "model_call", "final"}
    assert list((tmp_path / "reports" / "scenario_one").glob("*.json"))


@pytest.mark.asyncio
async def test_approval_scenario_persists_rejection_without_executing_write(
    tmp_path: Path,
) -> None:
    manifest = tmp_path / "scenario.json"
    _manifest(manifest, approval=True)
    services, _ = await _services(
        tmp_path,
        [
            _response(
                tool_calls=[
                    ToolCall(
                        id="write-1",
                        name="support__create_ticket",
                        arguments={
                            "customer_id": "cus_northwind",
                            "title": "Follow up",
                            "description": "Investigate safely",
                            "priority": "P2",
                            "category": "performance",
                        },
                    )
                ]
            ),
            _response(
                "{}",
                structured={
                    "claim_scores": [1.0],
                    "behavior_score": 1.0,
                    "final_structure_score": 1.0,
                },
            ),
        ],
        tools=True,
    )
    evaluation = await LiveScenarioEvaluator(
        services, reports_dir=tmp_path / "reports"
    ).evaluate(manifest)
    run = evaluation.runs[0]
    state = json.loads(
        (services.workspace.runs / "state" / f"{run.run_id}.state.json").read_text()
    )
    approval_id = state["outcome"]["pending_call"]["approval_id"]
    assert services.approvals.get(approval_id).status.value == "rejected"
    assert not any("approval checkpoint" in item for item in run.deterministic_failures)


def test_repeat_must_be_positive(tmp_path: Path) -> None:
    settings = HighlandSettings(workspace_dir=tmp_path / "workspace")
    services = ApplicationServices.build(settings)
    evaluator = LiveScenarioEvaluator(services, reports_dir=tmp_path / "reports")
    with pytest.raises(ValueError, match="positive"):
        __import__("asyncio").run(evaluator.evaluate(tmp_path / "missing.json", repeat=0))


class _WeeklyTools:
    customers: ClassVar[list[dict[str, str]]] = [
        {"id": "cus_northwind", "name": "Northwind", "status": "active", "tier": "enterprise"},
        {"id": "cus_alpine", "name": "Alpine", "status": "active", "tier": "enterprise"},
        {"id": "cus_lumon", "name": "Lumon", "status": "active", "tier": "enterprise"},
    ]

    def validate(self, name, arguments, *, scope, **_):  # type: ignore[no-untyped-def]
        customer_id = arguments.get("customer_id")
        if customer_id and scope.allowed_customers != frozenset({customer_id}):
            raise AssertionError("customer tool call escaped its loop scope")
        return ValidatedToolCall(
            qualified_name=name,
            arguments=arguments,
            policy=ToolPolicy(mode=ToolMode.READ),
            idempotency_key=None,
        )

    async def execute(self, call):  # type: ignore[no-untyped-def]
        payload = (
            {"items": self.customers}
            if call.qualified_name == "crm__list_customers"
            else {
                "items": [
                    {
                        "customer_id": call.arguments["customer_id"],
                        "signal": call.qualified_name,
                    }
                ]
            }
        )
        return NormalizedToolResult(
            connector=call.qualified_name.partition("__")[0],
            tool=call.qualified_name,
            content=json.dumps(payload),
            structured_content=payload,
        )


class _Gateway:
    tools: ClassVar[list[object]] = []

    def __init__(self, *_: object, **__: object) -> None:
        pass

    async def __aenter__(self):  # type: ignore[no-untyped-def]
        return self

    async def __aexit__(self, *_: object) -> None:
        return None


def _health(label: str, customer_id: str, evidence: list[str]) -> ChatResponse:
    return _response(
        "{}",
        structured={
            "classification": label,
            "rationale": f"Evidence-backed {label} for {customer_id}.",
            "evidence_ids": evidence,
        },
    )


@pytest.mark.asyncio
async def test_weekly_scenario_uses_canonical_runner_with_read_only_isolated_tools(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    judge = _response(
        "{}",
        structured={
            "claim_scores": [],
            "behavior_score": 1.0,
            "final_structure_score": 1.0,
        },
    )
    services, _ = await _services(
        tmp_path,
        [
            _health("watch", "cus_northwind", ["cus_northwind:S2"]),
            _health("healthy", "cus_alpine", []),
            _health("at_risk", "cus_lumon", ["cus_lumon:S3"]),
            judge,
        ],
    )
    tools = _WeeklyTools()

    def runner(_services, _registry):  # type: ignore[no-untyped-def]
        return WeeklyCustomerHealthRunner(
            model=services.provider.chat,
            tools=tools,
            artifacts=services.artifacts,
            runs=WeeklyHealthRunRepository(services.workspace.runs / "weekly-health"),
            events=services.run_events,
        )

    monkeypatch.setattr("highland.evaluation.scenarios.MCPGateway", _Gateway)
    monkeypatch.setattr("highland.evaluation.scenarios.ToolRegistry.from_file", lambda *_: object())
    monkeypatch.setattr(ApplicationServices, "weekly_health_runner", runner)
    manifest = Path(__file__).resolve().parents[3] / "data" / "scenarios" / "weekly-customer-health.json"
    evaluation = await LiveScenarioEvaluator(
        services, reports_dir=tmp_path / "reports"
    ).evaluate(manifest)
    assert evaluation.passed
    events = services.run_events.replay(evaluation.runs[0].run_id)
    calls = [event for event in events if event.type.value == "tool_call"]
    assert {event.payload["tool_call"]["name"].partition("__")[2] for event in calls} >= {
        "list_customers",
        "get_deployment",
        "list_customer_tickets",
        "list_incidents",
        "list_project_issues",
    }
    assert all(event.payload["mode"] == "read" for event in calls)
    assert all(
        event.payload["customer_scope"]
        == [event.payload["tool_call"]["arguments"]["customer_id"]]
        for event in calls
        if not event.payload["tool_call"]["name"].endswith("__list_customers")
    )
