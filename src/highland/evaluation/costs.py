from __future__ import annotations

from collections import defaultdict
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field

from highland.models.contracts import ChatResponse
from highland.models.pricing import PriceCatalog


class CostModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class EvaluationBudgetExceeded(RuntimeError):
    pass


class EvaluationCallCost(CostModel):
    scenario_id: str
    run_id: str
    node: str
    model: str
    calls: int = 1
    input_tokens: int = 0
    output_tokens: int = 0
    rerank_units: float = 0
    estimated_cost_usd: float | None = Field(default=None, ge=0)
    latency_ms: float | None = Field(default=None, ge=0)


class CostBreakdown(CostModel):
    dimension: str
    key: str
    calls: int
    input_tokens: int
    output_tokens: int
    rerank_units: float
    known_cost_usd: float
    unknown_price_calls: int
    latency_ms: float


class CostComparison(CostModel):
    baseline_path: str
    call_delta: int
    token_delta: int
    known_cost_delta_usd: float
    latency_delta_ms: float


class EvaluationCostReport(CostModel):
    warning_budget_usd: float
    hard_budget_usd: float
    warning_reached: bool
    known_cost_usd: float
    unknown_price_calls: int
    breakdowns: list[CostBreakdown]
    comparison: CostComparison | None = None


class EvaluationCostTracker:
    def __init__(
        self,
        prices: PriceCatalog,
        *,
        warning_budget_usd: float,
        hard_budget_usd: float,
    ) -> None:
        if warning_budget_usd < 0 or hard_budget_usd <= 0:
            raise ValueError("evaluation budgets must be non-negative and hard must be positive")
        if warning_budget_usd > hard_budget_usd:
            raise ValueError("warning budget cannot exceed hard budget")
        self.prices = prices
        self.warning_budget_usd = warning_budget_usd
        self.hard_budget_usd = hard_budget_usd
        self.calls: list[EvaluationCallCost] = []

    @property
    def known_cost_usd(self) -> float:
        return sum(call.estimated_cost_usd or 0 for call in self.calls)

    def before_call(self) -> None:
        if self.known_cost_usd >= self.hard_budget_usd:
            raise EvaluationBudgetExceeded(
                f"live evaluation hard budget reached (${self.hard_budget_usd:.4f})"
            )

    def record(
        self,
        response: ChatResponse,
        *,
        scenario_id: str,
        run_id: str,
        node: str,
        operation: str = "chat",
    ) -> None:
        self.calls.append(
            EvaluationCallCost(
                scenario_id=scenario_id,
                run_id=run_id,
                node=node,
                model=response.metadata.model,
                input_tokens=response.usage.input_tokens or 0,
                output_tokens=response.usage.output_tokens or 0,
                rerank_units=response.usage.search_units or 0,
                estimated_cost_usd=self.prices.estimate(
                    response.metadata.model, operation, response.usage
                ),
                latency_ms=response.metadata.latency_ms,
            )
        )

    def report(self, *, baseline: Path | None = None) -> EvaluationCostReport:
        breakdowns: list[CostBreakdown] = []
        for dimension, attribute in (
            ("scenario", "scenario_id"),
            ("run", "run_id"),
            ("node", "node"),
            ("model", "model"),
        ):
            grouped: dict[str, list[EvaluationCallCost]] = defaultdict(list)
            for call in self.calls:
                grouped[str(getattr(call, attribute))].append(call)
            for key, calls in sorted(grouped.items()):
                breakdowns.append(
                    CostBreakdown(
                        dimension=dimension,
                        key=key,
                        calls=len(calls),
                        input_tokens=sum(call.input_tokens for call in calls),
                        output_tokens=sum(call.output_tokens for call in calls),
                        rerank_units=sum(call.rerank_units for call in calls),
                        known_cost_usd=sum(call.estimated_cost_usd or 0 for call in calls),
                        unknown_price_calls=sum(
                            call.estimated_cost_usd is None for call in calls
                        ),
                        latency_ms=sum(call.latency_ms or 0 for call in calls),
                    )
                )
        report = EvaluationCostReport(
            warning_budget_usd=self.warning_budget_usd,
            hard_budget_usd=self.hard_budget_usd,
            warning_reached=self.known_cost_usd >= self.warning_budget_usd,
            known_cost_usd=self.known_cost_usd,
            unknown_price_calls=sum(call.estimated_cost_usd is None for call in self.calls),
            breakdowns=breakdowns,
        )
        if baseline:
            previous = EvaluationCostReport.model_validate_json(
                baseline.read_text(encoding="utf-8")
            )
            current_calls = sum(item.calls for item in breakdowns if item.dimension == "model")
            previous_calls = sum(
                item.calls for item in previous.breakdowns if item.dimension == "model"
            )
            current_tokens = sum(
                item.input_tokens + item.output_tokens
                for item in breakdowns
                if item.dimension == "model"
            )
            previous_tokens = sum(
                item.input_tokens + item.output_tokens
                for item in previous.breakdowns
                if item.dimension == "model"
            )
            current_latency = sum(
                item.latency_ms for item in breakdowns if item.dimension == "model"
            )
            previous_latency = sum(
                item.latency_ms
                for item in previous.breakdowns
                if item.dimension == "model"
            )
            report.comparison = CostComparison(
                baseline_path=str(baseline),
                call_delta=current_calls - previous_calls,
                token_delta=current_tokens - previous_tokens,
                known_cost_delta_usd=report.known_cost_usd - previous.known_cost_usd,
                latency_delta_ms=current_latency - previous_latency,
            )
        return report

    def write(self, path: Path, *, baseline: Path | None = None) -> EvaluationCostReport:
        report = self.report(baseline=baseline)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(report.model_dump_json(indent=2) + "\n", encoding="utf-8")
        markdown = path.with_suffix(".md")
        lines = [
            "# Live evaluation cost report",
            "",
            f"- Known estimated cost: ${report.known_cost_usd:.6f}",
            f"- Unknown-price calls: {report.unknown_price_calls}",
            f"- Warning budget: ${report.warning_budget_usd:.4f}",
            f"- Hard budget: ${report.hard_budget_usd:.4f}",
            "",
            "| Dimension | Key | Calls | Tokens | Rerank units | Cost | Unknown | Latency ms |",
            "| --- | --- | ---: | ---: | ---: | ---: | ---: | ---: |",
        ]
        for item in report.breakdowns:
            lines.append(
                f"| {item.dimension} | {item.key} | {item.calls} "
                f"| {item.input_tokens + item.output_tokens} | {item.rerank_units:g} "
                f"| ${item.known_cost_usd:.6f} | {item.unknown_price_calls} "
                f"| {item.latency_ms:.1f} |"
            )
        markdown.write_text("\n".join(lines) + "\n", encoding="utf-8")
        return report
