from __future__ import annotations

import hashlib
import json
import os
from datetime import UTC, datetime, timedelta
from enum import StrEnum
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict

from .mcp import NormalizedToolResult
from .policy import ToolMode, ToolPolicy, ToolRegistry, ValidatedToolCall


class ApprovalModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ApprovalStatus(StrEnum):
    PENDING = "pending"
    APPROVED = "approved"
    REJECTED = "rejected"
    COMPLETED = "completed"
    EXPIRED = "expired"


class ApprovalRequest(ApprovalModel):
    version: int = 1
    id: str
    run_id: str
    tool_call_id: str
    tool: str
    arguments: dict[str, Any]
    reason: str
    preview: str
    idempotency_key: str
    created_at: datetime
    expires_at: datetime
    status: ApprovalStatus = ApprovalStatus.PENDING
    decision_reason: str | None = None
    decided_at: datetime | None = None
    result: dict[str, Any] | None = None


class ApprovalStore:
    def __init__(self, directory: Path) -> None:
        self.directory = directory

    def create(
        self,
        *,
        run_id: str,
        tool_call_id: str,
        call: ValidatedToolCall,
        reason: str,
        preview: str | None = None,
        ttl: timedelta = timedelta(hours=24),
    ) -> ApprovalRequest:
        identity = f"{run_id}:{tool_call_id}:{call.qualified_name}"
        approval_id = f"apr_{hashlib.sha256(identity.encode()).hexdigest()[:20]}"
        path = self._path(approval_id)
        if path.exists():
            return self.get(approval_id)
        now = datetime.now(UTC)
        request = ApprovalRequest(
            id=approval_id,
            run_id=run_id,
            tool_call_id=tool_call_id,
            tool=call.qualified_name,
            arguments=call.arguments,
            reason=reason,
            preview=preview or json.dumps(call.arguments, sort_keys=True),
            idempotency_key=call.idempotency_key or "",
            created_at=now,
            expires_at=now + ttl,
        )
        self._save(request)
        return request

    def get(self, approval_id: str) -> ApprovalRequest:
        request = ApprovalRequest.model_validate_json(
            self._path(approval_id).read_text(encoding="utf-8")
        )
        if request.status is ApprovalStatus.PENDING and request.expires_at <= datetime.now(UTC):
            request = request.model_copy(update={"status": ApprovalStatus.EXPIRED})
            self._save(request)
        return request

    def decide(
        self,
        approval_id: str,
        *,
        approve: bool,
        reason: str | None = None,
    ) -> ApprovalRequest:
        request = self.get(approval_id)
        desired = ApprovalStatus.APPROVED if approve else ApprovalStatus.REJECTED
        if request.status is desired or request.status is ApprovalStatus.COMPLETED:
            return request
        if request.status is not ApprovalStatus.PENDING:
            raise ValueError(f"approval {approval_id} is already {request.status.value}")
        request = request.model_copy(
            update={
                "status": desired,
                "decision_reason": reason,
                "decided_at": datetime.now(UTC),
            }
        )
        self._save(request)
        return request

    def complete(self, approval_id: str, result: NormalizedToolResult) -> ApprovalRequest:
        request = self.get(approval_id)
        completed = request.model_copy(
            update={
                "status": ApprovalStatus.COMPLETED,
                "result": {
                    "connector": result.connector,
                    "tool": result.tool,
                    "content": result.content,
                    "structured_content": result.structured_content,
                    "is_error": result.is_error,
                    "error_type": result.error_type,
                },
            }
        )
        self._save(completed)
        return completed

    def _path(self, approval_id: str) -> Path:
        return self.directory / f"{approval_id}.json"

    def _save(self, request: ApprovalRequest) -> None:
        self.directory.mkdir(parents=True, exist_ok=True)
        target = self._path(request.id)
        temporary = target.with_suffix(".tmp")
        temporary.write_text(request.model_dump_json(indent=2), encoding="utf-8")
        os.replace(temporary, target)


class ApprovalService:
    def __init__(self, store: ApprovalStore, tools: ToolRegistry) -> None:
        self.store = store
        self.tools = tools

    async def resume(self, approval_id: str) -> NormalizedToolResult:
        request = self.store.get(approval_id)
        if request.status is ApprovalStatus.COMPLETED:
            return NormalizedToolResult(**request.result)  # type: ignore[arg-type]
        if request.status is ApprovalStatus.REJECTED:
            return NormalizedToolResult(
                connector=request.tool.partition("__")[0],
                tool=request.tool,
                content=f"Human rejected the requested action: {request.decision_reason or 'no reason'}",
                is_error=True,
                error_type="approval_rejected",
            )
        if request.status is not ApprovalStatus.APPROVED:
            raise ValueError(f"approval {approval_id} is {request.status.value}")
        call = ValidatedToolCall(
            qualified_name=request.tool,
            arguments=request.arguments,
            policy=ToolPolicy(
                mode=ToolMode.WRITE,
                approval_required=True,
                idempotent_with_key=True,
                customer_scoped=True,
            ),
            idempotency_key=request.idempotency_key,
        )
        result = await self.tools.execute_approved(call)
        self.store.complete(approval_id, result)
        return result
