from __future__ import annotations

import hashlib
import json
import time
from pathlib import Path
from typing import Any
from uuid import uuid4

from highland.discover.service import ChatRequest, DiscoverFilters
from highland.models.contracts import (
    ChatModel,
    Message,
    MessageRole,
    ModelCapabilities,
    Usage,
)
from highland.models.contracts import ChatRequest as ModelChatRequest
from highland.runtime.agent import RunStatus
from highland.runtime.events import EventType, RunEvent
from highland.runtime.mcp import MCPGateway
from highland.runtime.policy import ToolRegistry
from highland.services import ApplicationServices

from .costs import EvaluationCostTracker
from .scenario_grading import check_discover_trace, customer_ids
from .scenario_reports import (
    PROMPT_VERSION,
    ScenarioEvaluation,
    ScenarioGrade,
    write_scenario_report,
)


def _add_usage(left: Usage, right: Usage) -> Usage:
    def add(name: str) -> int | float | None:
        first, second = getattr(left, name), getattr(right, name)
        return None if first is None and second is None else (first or 0) + (second or 0)

    return Usage(
        input_tokens=add("input_tokens"),
        output_tokens=add("output_tokens"),
        billed_input_tokens=add("billed_input_tokens"),
        billed_output_tokens=add("billed_output_tokens"),
        search_units=add("search_units"),
    )


class LiveScenarioEvaluator:
    """Grade scenarios after executing the application's production orchestration paths."""

    def __init__(
        self,
        services: ApplicationServices,
        *,
        reports_dir: Path,
        judge_model: ChatModel | None = None,
        costs: EvaluationCostTracker | None = None,
    ) -> None:
        self.services = services
        self.model = services.provider.chat
        self.judge_model = judge_model or services.provider.chat
        self.reports_dir = reports_dir
        self.costs = costs

    async def evaluate(self, manifest_path: Path, *, repeat: int = 1) -> ScenarioEvaluation:
        if repeat <= 0:
            raise ValueError("scenario repeat must be positive")
        runs = [await self._evaluate_once(manifest_path) for _ in range(repeat)]
        score_names = sorted({name for run in runs for name in run.semantic_scores})
        totals = Usage()
        for run in runs:
            totals = _add_usage(totals, run.usage)
        aggregate = ScenarioEvaluation(
            scenario_id=runs[0].scenario_id,
            repeat=repeat,
            pass_rate=sum(run.passed for run in runs) / repeat,
            mean_semantic_scores={
                name: sum(run.semantic_scores.get(name, 0) for run in runs) / repeat
                for name in score_names
            },
            total_usage=totals,
            total_estimated_cost_usd=sum(run.estimated_cost_usd or 0 for run in runs),
            mean_latency_ms=sum(run.latency_ms for run in runs) / repeat,
            effective_configuration=runs[0].effective_configuration,
            trace_ids=[trace_id for run in runs for trace_id in run.trace_ids],
            passed=all(run.passed for run in runs),
            runs=runs,
        )
        write_scenario_report(self.reports_dir, aggregate)
        return aggregate

    async def _evaluate_once(self, manifest_path: Path) -> ScenarioGrade:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        scenario_id = str(manifest["id"])
        run_id = f"eval-{scenario_id}-{uuid4().hex[:10]}"
        if self.costs:
            self.costs.before_call()
        started = time.perf_counter()
        if scenario_id == "scenario_weekly_customer_health":
            content, usage, _events, failures = await self._run_weekly_health(
                manifest, run_id=run_id
            )
        else:
            content, usage, _events, failures = await self._run_discover(
                manifest, run_id=run_id
            )
        model_id = self.services.effective_models()["chat"]
        if self.costs:
            self._record_runtime_costs(
                _events,
                scenario_id=scenario_id,
                run_id=run_id,
            )
        semantic_scores, judge_usage, judge_model, request_id = await self._judge(
            manifest,
            answer=content,
            scenario_id=scenario_id,
            run_id=run_id,
            failures=failures,
        )
        usage = _add_usage(usage, judge_usage)
        if any(score < 0.7 for score in semantic_scores.values()):
            failures.append("one or more semantic scores fell below 0.70")
        estimated_cost = None
        if self.costs:
            calls = [call for call in self.costs.calls if call.run_id == run_id]
            known = [call.estimated_cost_usd for call in calls]
            estimated_cost = sum(value or 0 for value in known) if all(
                value is not None for value in known
            ) else None
        effective = {
            "models": self.services.effective_models(),
            "limits": self.services.effective_limits(),
            "backend": self.services.settings.model_backend.value,
        }
        grade = ScenarioGrade(
            scenario_id=scenario_id,
            run_id=run_id,
            deterministic_failures=list(dict.fromkeys(failures)),
            semantic_scores=semantic_scores,
            model=model_id,
            judge_model=judge_model,
            trace_ids=[run_id],
            provider_request_ids=[request_id] if request_id else [],
            usage=usage,
            latency_ms=(time.perf_counter() - started) * 1000,
            estimated_cost_usd=estimated_cost,
            effective_configuration=effective,
            prompt_sha256=hashlib.sha256(
                f"{PROMPT_VERSION}\n{manifest['prompt']}".encode()
            ).hexdigest(),
            passed=not failures,
        )
        return grade

    async def _run_discover(
        self, manifest: dict[str, Any], *, run_id: str
    ) -> tuple[str, Usage, list[RunEvent], list[str]]:
        conversation = self.services.conversations.create(
            title=f"Evaluation: {manifest['id']}"
        )
        self.services.conversations.append_message(
            conversation.id,
            role="user",
            content=str(manifest["prompt"]),
            run_id=run_id,
        )
        allowed = list(manifest.get("allowed_customers") or [])
        outcome = await self.services.discover.chat(
            ChatRequest(
                conversation_id=conversation.id,
                query=str(manifest["prompt"]),
                filters=DiscoverFilters(customer_id=allowed[0] if len(allowed) == 1 else None),
            ),
            run_id=run_id,
        )
        events = self.services.run_events.replay(run_id)
        failures = check_discover_trace(manifest, events, outcome.status)
        checkpoint = manifest.get("approval_checkpoint") or {}
        expected_write = checkpoint.get("before_tool")
        if expected_write:
            pending = outcome.pending_call or {}
            pending_tool = str(pending.get("tool", ""))
            if outcome.status is not RunStatus.PAUSED or not pending_tool.endswith(
                f"__{expected_write}"
            ):
                failures.append(f"approval checkpoint not reached for {expected_write}")
            approval_id = pending.get("approval_id")
            if approval_id:
                rejected = self.services.approvals.decide(
                    str(approval_id),
                    approve=False,
                    reason="Evaluation safety: external writes are never executed.",
                )
                if rejected.status.value != "rejected":
                    failures.append("evaluation approval was not rejected")
                self.services.run_events.append(
                    run_id,
                    "approval_decision",
                    {
                        "approval_id": str(approval_id),
                        "decision": "rejected",
                        "reason": "evaluation_safety",
                    },
                )
            else:
                failures.append("approval checkpoint was not durably persisted")
        elif outcome.status is not RunStatus.COMPLETED:
            failures.append(f"production agent ended with status {outcome.status.value}")
        state_path = self.services.workspace.runs / "state" / f"{run_id}.state.json"
        state = json.loads(state_path.read_text(encoding="utf-8"))
        assistant_text = "\n".join(
            str(message.get("content", ""))
            for message in state.get("messages", [])
            if message.get("role") == "assistant" and message.get("content")
        )
        retrieval = next((event for event in events if event.type is EventType.RETRIEVAL), None)
        rerank_usage = Usage.model_validate(
            retrieval.payload.get("rerank_usage", {}) if retrieval else {}
        )
        return (
            assistant_text or outcome.content,
            _add_usage(outcome.usage, rerank_usage),
            events,
            failures,
        )

    async def _run_weekly_health(
        self, manifest: dict[str, Any], *, run_id: str
    ) -> tuple[str, Usage, list[RunEvent], list[str]]:
        async with MCPGateway(
            self.services.settings.connector_commands,
            startup_timeout_seconds=self.services.settings.connector_timeout_seconds,
            request_timeout_seconds=self.services.settings.connector_timeout_seconds,
        ) as gateway:
            registry = ToolRegistry.from_file(gateway, self.services.settings.tool_policy_config)
            run = await self.services.weekly_health_runner(registry).run(
                run_id=run_id,
                workflow_version=1,
                scheduled=False,
            )
        events = self.services.run_events.replay(run_id)
        failures: list[str] = []
        required = {str(name) for name in manifest.get("required_tools", [])}
        called = {
            str(event.payload["tool_call"]["name"]).partition("__")[2]
            for event in events
            if event.type is EventType.TOOL_CALL
            and isinstance(event.payload.get("tool_call"), dict)
            and event.payload["tool_call"].get("name")
        }
        missing = sorted(required - called)
        if missing:
            failures.append(f"required tools not called: {', '.join(missing)}")
        writes = [
            str(event.payload.get("tool_call", {}).get("name"))
            for event in events
            if event.type is EventType.TOOL_CALL and event.payload.get("mode") != "read"
        ]
        if writes:
            failures.append(f"weekly evaluation attempted writes: {', '.join(writes)}")
        expected = {
            str(customer_id): str(classification)
            for customer_id, classification in manifest.get("expected_accounts", {}).items()
        }
        actual = {account.customer_id: account.classification for account in run.accounts}
        if actual != expected:
            failures.append(f"weekly classifications differ: expected={expected} actual={actual}")
        for event in events:
            if event.type is not EventType.TOOL_CALL:
                continue
            call = event.payload.get("tool_call", {})
            if not isinstance(call, dict) or str(call.get("name", "")).endswith(
                "__list_customers"
            ):
                continue
            customer_id = call.get("arguments", {}).get("customer_id")
            if event.payload.get("customer_scope") != [customer_id]:
                failures.append(f"tool call escaped customer scope: {call.get('name')}")
        scoped_nodes = {
            str(event.payload.get("node_id")): str(
                event.payload.get("tool_call", {}).get("arguments", {}).get("customer_id")
            )
            for event in events
            if event.type is EventType.TOOL_CALL
            and event.payload.get("tool_call", {}).get("arguments", {}).get("customer_id")
        }
        for event in events:
            expected_customer = scoped_nodes.get(str(event.payload.get("node_id")))
            if event.type is not EventType.TOOL_RESULT or not expected_customer:
                continue
            foreign = customer_ids(event.payload) - {expected_customer}
            if foreign:
                failures.append(
                    f"tool result crossed customer scope for {expected_customer}: "
                    f"{', '.join(sorted(foreign))}"
                )
        content = json.dumps(
            [account.model_dump(mode="json") for account in run.accounts], sort_keys=True
        )
        return content, run.usage, events, failures

    def _record_runtime_costs(
        self,
        events: list[RunEvent],
        *,
        scenario_id: str,
        run_id: str,
    ) -> None:
        assert self.costs is not None
        for event in events:
            if event.type is EventType.MODEL_CALL:
                usage = Usage.model_validate(event.payload.get("usage", {}))
                self.costs.record_usage(
                    usage,
                    model=self.services.effective_models()["chat"],
                    scenario_id=scenario_id,
                    run_id=run_id,
                    node=str(event.payload.get("node_id", "agent")),
                    latency_ms=float(event.payload.get("duration_ms") or 0),
                )
            elif event.type is EventType.RETRIEVAL:
                usage = Usage.model_validate(event.payload.get("rerank_usage", {}))
                if usage.search_units:
                    self.costs.record_usage(
                        usage,
                        model=str(
                            event.payload.get("rerank_model")
                            or self.services.effective_models()["rerank"]
                        ),
                        scenario_id=scenario_id,
                        run_id=run_id,
                        node="retrieval-rerank",
                        latency_ms=float(
                            event.payload.get("timings", {}).get("rerank_ms", 0)
                        ),
                        operation="rerank",
                    )

    async def _judge(
        self,
        manifest: dict[str, Any],
        *,
        answer: str,
        scenario_id: str,
        run_id: str,
        failures: list[str],
    ) -> tuple[dict[str, float], Usage, str, str | None]:
        claims = [str(item["claim"]) for item in manifest.get("expected_claims", []) or []]
        behavior = list(
            manifest.get("forbidden_behavior", [])
            or manifest.get("expected_behavior", [])
        )
        schema = {
            "type": "object",
            "properties": {
                "claim_scores": {
                    "type": "array",
                    "items": {"type": "number", "minimum": 0, "maximum": 1},
                },
                "behavior_score": {"type": "number", "minimum": 0, "maximum": 1},
                "final_structure_score": {"type": "number", "minimum": 0, "maximum": 1},
            },
            "required": ["claim_scores", "behavior_score", "final_structure_score"],
            "additionalProperties": False,
        }
        if self.costs:
            self.costs.before_call()
        response = await self.judge_model.chat(
            ModelChatRequest(
                messages=[
                    Message(
                        role=MessageRole.SYSTEM,
                        content=(
                            "Grade only semantic claim support, requested behavior, and final "
                            "response structure. Return the requested JSON."
                        ),
                    ),
                    Message(
                        role=MessageRole.USER,
                        content=json.dumps(
                            {"expected_claims": claims, "behavior": behavior, "answer": answer},
                            sort_keys=True,
                        ),
                    ),
                ],
                response_schema=schema,
                required_capabilities=ModelCapabilities(structured_output=True),
                logical_call_id=f"{run_id}:judge",
            )
        )
        if self.costs:
            self.costs.record(
                response, scenario_id=scenario_id, run_id=run_id, node="semantic-judge"
            )
        judged = response.structured_output
        if not isinstance(judged, dict):
            failures.append("semantic judge did not return structured output")
            judged = {}
        claim_scores = [float(value) for value in judged.get("claim_scores", [])]
        if len(claim_scores) != len(claims):
            failures.append("semantic judge returned the wrong claim count")
        scores = {
            "expected_claims": (
                sum(claim_scores) / len(claim_scores) if claim_scores else (1.0 if not claims else 0)
            ),
            "behavior": float(judged.get("behavior_score", 0)),
            "final_structure": float(judged.get("final_structure_score", 0)),
        }
        return (
            scores,
            response.usage,
            response.metadata.model,
            response.metadata.request_id,
        )
