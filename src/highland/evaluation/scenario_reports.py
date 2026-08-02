from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from highland.models.contracts import Usage

PROMPT_VERSION = "scenario-eval-v2"


class ScenarioGrade(BaseModel):
    model_config = ConfigDict(extra="forbid")
    scenario_id: str
    run_id: str
    deterministic_failures: list[str] = Field(default_factory=list)
    semantic_scores: dict[str, float] = Field(default_factory=dict)
    model: str
    judge_model: str
    trace_ids: list[str] = Field(default_factory=list)
    provider_request_ids: list[str] = Field(default_factory=list)
    usage: Usage = Field(default_factory=Usage)
    latency_ms: float = Field(ge=0)
    estimated_cost_usd: float | None = Field(default=None, ge=0)
    effective_configuration: dict[str, Any] = Field(default_factory=dict)
    prompt_version: str = PROMPT_VERSION
    prompt_sha256: str
    passed: bool


class ScenarioEvaluation(BaseModel):
    model_config = ConfigDict(extra="forbid")
    scenario_id: str
    repeat: int = Field(ge=1)
    pass_rate: float = Field(ge=0, le=1)
    mean_semantic_scores: dict[str, float]
    total_usage: Usage
    total_estimated_cost_usd: float
    mean_latency_ms: float
    effective_configuration: dict[str, Any]
    trace_ids: list[str]
    passed: bool
    runs: list[ScenarioGrade]


def write_scenario_report(reports_dir: Path, evaluation: ScenarioEvaluation) -> None:
    target = reports_dir / evaluation.scenario_id
    target.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    (target / f"{timestamp}.json").write_text(
        evaluation.model_dump_json(indent=2) + "\n", encoding="utf-8"
    )
    lines = [
        f"# Scenario evaluation: {evaluation.scenario_id}",
        "",
        f"- Result: {'PASS' if evaluation.passed else 'FAIL'}",
        f"- Repeats: {evaluation.repeat}",
        f"- Pass rate: {evaluation.pass_rate:.1%}",
        f"- Mean latency: {evaluation.mean_latency_ms:.1f} ms",
        f"- Estimated cost: ${evaluation.total_estimated_cost_usd:.6f}",
        f"- Trace IDs: {', '.join(evaluation.trace_ids)}",
        "",
        "## Mean model-judged scores",
        "",
        *(f"- {name}: {score:.2f}" for name, score in evaluation.mean_semantic_scores.items()),
        "",
        "## Deterministic failures by run",
        "",
    ]
    for run in evaluation.runs:
        lines.append(f"- `{run.run_id}`: {', '.join(run.deterministic_failures) or 'none'}")
    (target / f"{timestamp}.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
