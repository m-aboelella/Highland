from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import Any

from jsonschema import Draft202012Validator

from .mcp import MCPGateway, MCPTool, NormalizedToolResult


class ToolMode(StrEnum):
    READ = "read"
    WRITE = "write"


@dataclass(frozen=True, slots=True)
class RunScope:
    allowed_customers: frozenset[str] = frozenset()
    allowed_visibilities: frozenset[str] = frozenset()


@dataclass(frozen=True, slots=True)
class ToolPolicy:
    mode: ToolMode
    approval_required: bool = False
    idempotent_with_key: bool = False
    customer_scoped: bool = False


@dataclass(frozen=True, slots=True)
class ValidatedToolCall:
    qualified_name: str
    arguments: dict[str, Any]
    policy: ToolPolicy
    idempotency_key: str | None


class ToolRejected(ValueError):
    def __init__(self, message: str, *, code: str) -> None:
        super().__init__(message)
        self.code = code

    def as_result(self, tool: str) -> NormalizedToolResult:
        return NormalizedToolResult(
            connector=tool.partition("__")[0] or "unknown",
            tool=tool,
            content=str(self)[:1000],
            is_error=True,
            error_type=self.code,
        )


class ToolRegistry:
    def __init__(self, gateway: MCPGateway, policies: dict[str, ToolPolicy]) -> None:
        self.gateway = gateway
        self._tools = {tool.qualified_name: tool for tool in gateway.tools}
        self._policies = policies

    @classmethod
    def from_file(cls, gateway: MCPGateway, path: Path) -> ToolRegistry:
        payload = json.loads(path.read_text(encoding="utf-8"))
        if payload.get("version") != 1:
            raise ValueError("unsupported tool policy version")
        policies = {
            name: ToolPolicy(
                mode=ToolMode(item["mode"]),
                approval_required=bool(item.get("approval_required", False)),
                idempotent_with_key=bool(item.get("idempotent_with_key", False)),
                customer_scoped=bool(item.get("customer_scoped", False)),
            )
            for name, item in payload["tools"].items()
            if not item.get("future")
        }
        return cls(gateway, policies)

    def policy_for(self, tool: MCPTool) -> ToolPolicy:
        return self._policies.get(tool.source_name, self._policies["*"])

    def model_tools(self) -> list[Any]:
        return self.gateway.model_tools()

    def validate(
        self,
        qualified_name: str,
        arguments: dict[str, Any],
        *,
        run_id: str,
        logical_step_id: str,
        scope: RunScope,
    ) -> ValidatedToolCall:
        tool = self._tools.get(qualified_name)
        if tool is None:
            raise ToolRejected(
                f"Unknown or unavailable tool {qualified_name!r}",
                code="unknown_tool",
            )
        policy = self.policy_for(tool)
        supplied = dict(arguments)
        generated_key = None
        if policy.idempotent_with_key:
            generated_key = hashlib.sha256(
                f"{run_id}:{logical_step_id}:{qualified_name}".encode()
            ).hexdigest()
            supplied["idempotency_key"] = generated_key
        errors = sorted(
            Draft202012Validator(tool.input_schema).iter_errors(supplied),
            key=lambda error: list(error.path),
        )
        if errors:
            message = "; ".join(error.message for error in errors[:3])
            raise ToolRejected(
                f"Invalid arguments for {qualified_name}: {message}",
                code="invalid_arguments",
            )
        customer = supplied.get("customer_id")
        if policy.customer_scoped and not isinstance(customer, str):
            raise ToolRejected("Customer-scoped tool requires customer_id", code="customer_scope")
        if customer is not None and scope.allowed_customers and customer not in scope.allowed_customers:
            raise ToolRejected(f"Customer {customer!r} is outside this run's scope", code="customer_scope")
        visibility = supplied.get("visibility")
        if (
            visibility is not None
            and scope.allowed_visibilities
            and visibility not in scope.allowed_visibilities
        ):
            raise ToolRejected(
                f"Visibility {visibility!r} is outside this run's scope",
                code="visibility_scope",
            )
        return ValidatedToolCall(
            qualified_name=qualified_name,
            arguments=supplied,
            policy=policy,
            idempotency_key=generated_key,
        )

    async def execute(self, call: ValidatedToolCall) -> NormalizedToolResult:
        if call.policy.approval_required:
            raise ToolRejected("Tool call requires approval", code="approval_required")
        return await self.gateway.call(call.qualified_name, call.arguments)
