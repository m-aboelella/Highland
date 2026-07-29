from __future__ import annotations

import json
from enum import StrEnum
from pathlib import Path
from typing import Annotated

from pydantic import BeforeValidator, Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict

REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_CONNECTOR_COMMANDS = {
    name: ("highland-mcp", name)
    for name in ("crm", "knowledge", "support", "observability", "communications", "projects")
}


class ModelBackend(StrEnum):
    SCRIPTED = "scripted"
    COHERE = "cohere"


def _connector_commands(value: object) -> object:
    if isinstance(value, str):
        decoded = json.loads(value)
        if not isinstance(decoded, dict):
            raise TypeError("connector commands must be a JSON object")
        value = decoded
    if isinstance(value, dict):
        return {name: tuple(command) for name, command in value.items()}
    return value


ConnectorCommands = Annotated[dict[str, tuple[str, ...]], BeforeValidator(_connector_commands)]


class HighlandSettings(BaseSettings):
    """Environment-backed application settings with no runtime side effects."""

    model_config = SettingsConfigDict(
        env_prefix="HIGHLAND_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        validate_default=True,
    )

    workspace_dir: Path = REPO_ROOT / "var" / "highland"
    workspace_name: str = "Highland"
    model_price_config: Path = REPO_ROOT / "config" / "model_prices.json"
    tool_policy_config: Path = REPO_ROOT / "config" / "tool_policy.json"
    agent_profile_config: Path = REPO_ROOT / "config" / "agents" / "general.json"
    mock_catalog_url: str = "http://127.0.0.1:8099"
    connector_commands: ConnectorCommands = Field(
        default_factory=lambda: dict(DEFAULT_CONNECTOR_COMMANDS)
    )

    model_backend: ModelBackend = ModelBackend.SCRIPTED
    chat_model: str = "command-a-plus-05-2026"
    embedding_model: str = "embed-v4.0"
    rerank_model: str = "rerank-v4.0-fast"
    cohere_api_key: SecretStr | None = Field(
        default=None,
        validation_alias="COHERE_API_KEY",
    )

    request_timeout_seconds: float = Field(default=30.0, gt=0)
    connector_timeout_seconds: float = Field(default=15.0, gt=0)
    max_steps_per_run: int = Field(default=20, gt=0)
    max_model_calls_per_run: int = Field(default=10, gt=0)
    max_rerank_searches_per_run: int = Field(default=10, gt=0)
    max_tokens_per_run: int = Field(default=100_000, gt=0)
    max_run_cost_usd: float = Field(default=0.10, gt=0)
    monthly_budget_usd: float = Field(default=5.00, gt=0)
    evaluation_warning_budget_usd: float = Field(default=0.10, ge=0)
    evaluation_hard_budget_usd: float = Field(default=0.25, gt=0)
    host: str = "127.0.0.1"
    port: int = Field(default=8080, ge=1, le=65535)
