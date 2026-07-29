from __future__ import annotations

import asyncio
import json
import os
import time
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from highland.models.contracts import (
    ChatModel,
    ChatRequest,
    Citation,
    Document,
    FinishReason,
    Message,
    MessageRole,
    ModelCapabilities,
    ToolResult,
    Usage,
)

from .approvals import ApprovalStore
from .events import RunEventStore
from .policy import RunScope, ToolRegistry, ToolRejected, ValidatedToolCall


class RuntimeModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class AgentBudgets(RuntimeModel):
    max_steps: int = Field(gt=0)
    max_model_calls: int = Field(gt=0)
    max_wall_seconds: float = Field(gt=0)
    max_tool_result_chars: int = Field(gt=0)
    max_context_chars: int = Field(gt=0)


class AgentProfile(RuntimeModel):
    version: int
    id: str
    instructions: str
    model: str
    allowed_tools: list[str]
    retrieval_defaults: dict[str, Any]
    budgets: AgentBudgets

    @classmethod
    def load(cls, path: Path) -> AgentProfile:
        return cls.model_validate_json(path.read_text(encoding="utf-8"))


class RunStatus(StrEnum):
    COMPLETED = "completed"
    PAUSED = "paused"
    MAX_STEPS = "max_steps"
    FAILED = "failed"


class RunOutcome(RuntimeModel):
    run_id: str
    status: RunStatus
    content: str = ""
    finish_reason: FinishReason = FinishReason.UNKNOWN
    citations: list[Citation] = Field(default_factory=list)
    usage: Usage = Field(default_factory=Usage)
    pending_call: dict[str, Any] | None = None


@dataclass(slots=True)
class RunRepository:
    directory: Path

    def save(self, run_id: str, payload: dict[str, Any]) -> None:
        self.directory.mkdir(parents=True, exist_ok=True)
        target = self.directory / f"{run_id}.state.json"
        temporary = target.with_suffix(".tmp")
        temporary.write_text(json.dumps(payload, sort_keys=True, indent=2), encoding="utf-8")
        os.replace(temporary, target)

    def load(self, run_id: str) -> dict[str, Any]:
        return json.loads((self.directory / f"{run_id}.state.json").read_text(encoding="utf-8"))


class AgentLoop:
    def __init__(
        self,
        model: ChatModel,
        tools: ToolRegistry,
        profile: AgentProfile,
        repository: RunRepository,
        approvals: ApprovalStore | None = None,
        event_store: RunEventStore | None = None,
    ) -> None:
        self.model = model
        self.tools = tools
        self.profile = profile
        self.repository = repository
        self.approvals = approvals
        self.event_store = event_store

    async def run(
        self,
        *,
        run_id: str,
        user_message: str,
        scope: RunScope | None = None,
        documents: list[Document] | None = None,
    ) -> RunOutcome:
        scope = scope or RunScope()
        messages = [
            Message(role=MessageRole.SYSTEM, content=self.profile.instructions),
            Message(role=MessageRole.USER, content=user_message),
        ]
        events: list[dict[str, Any]] = [{"type": "run_started", "run_id": run_id}]
        started = time.monotonic()
        totals = Usage(input_tokens=0, output_tokens=0)
        for step in range(1, self.profile.budgets.max_steps + 1):
            if step > self.profile.budgets.max_model_calls:
                break
            if time.monotonic() - started >= self.profile.budgets.max_wall_seconds:
                break
            request = ChatRequest(
                messages=self._bounded_messages(messages),
                tools=self.tools.model_tools(),
                documents=documents or [],
                required_capabilities=ModelCapabilities(tools=True),
                logical_call_id=f"{run_id}:model:{step}",
            )
            response = await self.model.chat(request)
            events.append(
                {
                    "type": "model_call",
                    "step": step,
                    "finish_reason": response.finish_reason.value,
                    "usage": response.usage.model_dump(mode="json"),
                }
            )
            events.append(
                {
                    "type": "usage",
                    "step": step,
                    **response.usage.model_dump(mode="json"),
                }
            )
            events.extend(
                {"type": "citation", **citation.model_dump(mode="json")}
                for citation in response.citations
            )
            totals = _add_usage(totals, response.usage)
            messages.append(response.message)
            if not response.message.tool_calls:
                events.append({"type": "final", "content": response.message.content})
                outcome = RunOutcome(
                    run_id=run_id,
                    status=RunStatus.COMPLETED,
                    content=response.message.content,
                    finish_reason=response.finish_reason,
                    citations=response.citations,
                    usage=totals,
                )
                self._save(run_id, messages, events, outcome)
                return outcome

            validated: list[tuple[Any, ValidatedToolCall]] = []
            immediate: list[ToolResult] = []
            for call in response.message.tool_calls:
                events.append({"type": "tool_call", "tool_call": call.model_dump(mode="json")})
                try:
                    checked = self.tools.validate(
                        call.name,
                        dict(call.arguments),
                        run_id=run_id,
                        logical_step_id=call.id,
                        scope=scope,
                    )
                except ToolRejected as error:
                    immediate.append(
                        ToolResult(
                            tool_call_id=call.id,
                            content=str(error)[: self.profile.budgets.max_tool_result_chars],
                            is_error=True,
                        )
                    )
                    continue
                if checked.policy.approval_required:
                    approval = (
                        self.approvals.create(
                            run_id=run_id,
                            tool_call_id=call.id,
                            call=checked,
                            reason=f"{checked.qualified_name} changes an external mock system",
                        )
                        if self.approvals
                        else None
                    )
                    outcome = RunOutcome(
                        run_id=run_id,
                        status=RunStatus.PAUSED,
                        finish_reason=FinishReason.TOOL_CALL,
                        usage=totals,
                        pending_call={
                            "tool_call_id": call.id,
                            "tool": checked.qualified_name,
                            "arguments": checked.arguments,
                            "idempotency_key": checked.idempotency_key,
                            "approval_id": approval.id if approval else None,
                        },
                    )
                    events.append({"type": "approval_required", **outcome.pending_call})
                    self._save(run_id, messages, events, outcome)
                    return outcome
                validated.append((call, checked))
            executed = await asyncio.gather(
                *(self.tools.execute(checked) for _, checked in validated)
            )
            tool_results = list(immediate)
            for (call, _), result in zip(validated, executed, strict=True):
                tool_results.append(
                    ToolResult(
                        tool_call_id=call.id,
                        content=result.content[: self.profile.budgets.max_tool_result_chars],
                        is_error=result.is_error,
                    )
                )
            for result in tool_results:
                events.append({"type": "tool_result", **result.model_dump(mode="json")})
            messages.append(Message(role=MessageRole.TOOL, tool_results=tool_results))
            self._save(run_id, messages, events, None)
        outcome = RunOutcome(
            run_id=run_id,
            status=RunStatus.MAX_STEPS,
            finish_reason=FinishReason.ERROR,
            usage=totals,
        )
        events.append({"type": "run_failed", "reason": "maximum steps or runtime budget"})
        self._save(run_id, messages, events, outcome)
        return outcome

    def _bounded_messages(self, messages: list[Message]) -> list[Message]:
        budget = self.profile.budgets.max_context_chars
        selected: list[Message] = []
        used = 0
        for message in reversed(messages):
            size = len(message.model_dump_json())
            if selected and used + size > budget:
                break
            selected.append(message)
            used += size
        return list(reversed(selected))

    def _save(
        self,
        run_id: str,
        messages: list[Message],
        events: list[dict[str, Any]],
        outcome: RunOutcome | None,
    ) -> None:
        if self.event_store:
            persisted = len(self.event_store.replay(run_id))
            for event in events[persisted:]:
                payload = {key: value for key, value in event.items() if key != "type"}
                self.event_store.append(run_id, event["type"], payload)
        self.repository.save(
            run_id,
            {
                "version": 1,
                "profile_id": self.profile.id,
                "messages": [message.model_dump(mode="json") for message in messages],
                "events": events,
                "outcome": outcome.model_dump(mode="json") if outcome else None,
            },
        )


def _add_usage(left: Usage, right: Usage) -> Usage:
    def add(name: str) -> int | float | None:
        first = getattr(left, name)
        second = getattr(right, name)
        return None if first is None and second is None else (first or 0) + (second or 0)

    return Usage(
        input_tokens=add("input_tokens"),
        output_tokens=add("output_tokens"),
        billed_input_tokens=add("billed_input_tokens"),
        billed_output_tokens=add("billed_output_tokens"),
        search_units=add("search_units"),
    )
