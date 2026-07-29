from __future__ import annotations

from datetime import date
from pathlib import Path

import pytest

from highland.evaluation.costs import (
    EvaluationBudgetExceeded,
    EvaluationCostTracker,
)
from highland.models.contracts import (
    ChatResponse,
    FinishReason,
    Message,
    MessageRole,
    ResponseMetadata,
    Usage,
)
from highland.models.pricing import ModelPrice, PriceCatalog


def response(*, model: str = "priced", tokens: int = 100, latency: float = 5) -> ChatResponse:
    return ChatResponse(
        message=Message(role=MessageRole.ASSISTANT, content="private prompt is not stored"),
        finish_reason=FinishReason.COMPLETE,
        usage=Usage(input_tokens=tokens, output_tokens=tokens),
        metadata=ResponseMetadata(
            provider="test", model=model, latency_ms=latency, request_id="trace"
        ),
    )


def prices() -> PriceCatalog:
    return PriceCatalog(
        prices=[
            ModelPrice(
                model="priced",
                effective_date=date(2026, 1, 1),
                input_usd_per_million_tokens=1000,
                output_usd_per_million_tokens=1000,
            )
        ]
    )


def test_cost_report_groups_dimensions_and_exposes_unknown_prices(tmp_path: Path) -> None:
    tracker = EvaluationCostTracker(
        prices(), warning_budget_usd=0.1, hard_budget_usd=1
    )
    tracker.record(response(), scenario_id="one", run_id="run-1", node="scenario")
    tracker.record(
        response(model="unknown"), scenario_id="one", run_id="run-1", node="judge"
    )
    report = tracker.write(tmp_path / "report.json")
    assert report.known_cost_usd == pytest.approx(0.2)
    assert report.unknown_price_calls == 1
    assert report.warning_reached
    assert {item.dimension for item in report.breakdowns} == {
        "scenario",
        "run",
        "node",
        "model",
    }
    raw = (tmp_path / "report.json").read_text()
    assert "private prompt" not in raw
    assert "api_key" not in raw


def test_hard_budget_aborts_before_next_call() -> None:
    tracker = EvaluationCostTracker(
        prices(), warning_budget_usd=0.1, hard_budget_usd=0.2
    )
    tracker.record(response(), scenario_id="one", run_id="run-1", node="scenario")
    with pytest.raises(EvaluationBudgetExceeded):
        tracker.before_call()


def test_baseline_comparison_uses_stored_estimates_not_new_prices(tmp_path: Path) -> None:
    baseline_tracker = EvaluationCostTracker(
        prices(), warning_budget_usd=1, hard_budget_usd=2
    )
    baseline_tracker.record(
        response(tokens=50, latency=3), scenario_id="one", run_id="old", node="scenario"
    )
    baseline = tmp_path / "baseline.json"
    baseline_tracker.write(baseline)
    current = EvaluationCostTracker(prices(), warning_budget_usd=1, hard_budget_usd=2)
    current.record(
        response(tokens=100, latency=5), scenario_id="one", run_id="new", node="scenario"
    )
    comparison = current.report(baseline=baseline).comparison
    assert comparison is not None
    assert comparison.token_delta == 100
    assert comparison.known_cost_delta_usd == pytest.approx(0.1)
    assert comparison.latency_delta_ms == 2
