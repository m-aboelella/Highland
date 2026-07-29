from __future__ import annotations

from datetime import date

import pytest

from highland.models.budgets import (
    BudgetController,
    BudgetedChatModel,
    BudgetedRerankModel,
    BudgetExceededError,
    BudgetLimits,
)
from highland.models.contracts import (
    ChatRequest,
    ChatResponse,
    Document,
    FinishReason,
    Message,
    MessageRole,
    RerankRequest,
    ResponseMetadata,
    Usage,
)
from highland.models.pricing import ModelPrice, PriceCatalog
from highland.models.scripted import (
    ScriptedChatModel,
    ScriptedRerankModel,
    ScriptedRerankStep,
)
from highland.storage.cost_ledger import CostLedger


def prices(
    *,
    input_price: float | None = 0,
    output_price: float | None = 0,
    search_price: float | None = 0,
) -> PriceCatalog:
    return PriceCatalog(
        prices=[
            ModelPrice(
                model="test-chat",
                effective_date=date(2026, 1, 1),
                input_usd_per_million_tokens=input_price,
                output_usd_per_million_tokens=output_price,
            ),
            ModelPrice(
                model="test-rerank",
                effective_date=date(2026, 1, 1),
                search_usd_per_unit=search_price,
            ),
        ]
    )


def limits(**changes) -> BudgetLimits:
    values = {
        "max_model_calls_per_run": 10,
        "max_rerank_searches_per_run": 10,
        "max_tokens_per_run": 100,
        "max_run_cost_usd": 10.0,
        "monthly_budget_usd": 100.0,
    }
    values.update(changes)
    return BudgetLimits(**values)


def chat_response(usage: Usage) -> ChatResponse:
    return ChatResponse(
        message=Message(role=MessageRole.ASSISTANT, content="scripted"),
        finish_reason=FinishReason.COMPLETE,
        usage=usage,
        metadata=ResponseMetadata(provider="scripted", model="test-chat", simulated=True),
    )


def controller(tmp_path, *, configured_limits: BudgetLimits, catalog=None) -> BudgetController:
    return BudgetController(
        run_id="run_1",
        limits=configured_limits,
        prices=catalog or prices(),
        ledger=CostLedger(tmp_path / "ledger.jsonl"),
    )


@pytest.mark.asyncio
async def test_runaway_chat_stops_at_call_boundary(tmp_path) -> None:
    response = chat_response(Usage(input_tokens=1, output_tokens=1))
    budget = controller(
        tmp_path,
        configured_limits=limits(max_model_calls_per_run=2),
    )
    provider = BudgetedChatModel(
        ScriptedChatModel([response, response, response], model="test-chat"),
        budget,
    )
    request = ChatRequest(messages=[Message(role=MessageRole.USER, content="loop")])

    await provider.chat(request)
    await provider.chat(request)
    with pytest.raises(BudgetExceededError) as caught:
        await provider.chat(request)

    assert caught.value.boundary == "model_calls"


@pytest.mark.asyncio
async def test_runaway_chat_stops_at_token_boundary_before_next_call(tmp_path) -> None:
    response = chat_response(Usage(input_tokens=4, output_tokens=2))
    budget = controller(tmp_path, configured_limits=limits(max_tokens_per_run=5))
    provider = BudgetedChatModel(
        ScriptedChatModel([response, response], model="test-chat"),
        budget,
    )
    request = ChatRequest(messages=[Message(role=MessageRole.USER, content="loop")])

    await provider.chat(request)
    with pytest.raises(BudgetExceededError) as caught:
        await provider.chat(request)

    assert caught.value.boundary == "tokens"


@pytest.mark.asyncio
async def test_runaway_chat_stops_at_estimated_cost_boundary(tmp_path) -> None:
    response = chat_response(Usage(billed_input_tokens=1))
    budget = controller(
        tmp_path,
        configured_limits=limits(max_run_cost_usd=0.5),
        catalog=prices(input_price=1_000_000),
    )
    provider = BudgetedChatModel(
        ScriptedChatModel([response, response], model="test-chat"),
        budget,
    )
    request = ChatRequest(messages=[Message(role=MessageRole.USER, content="loop")])

    await provider.chat(request)
    with pytest.raises(BudgetExceededError) as caught:
        await provider.chat(request)

    assert caught.value.boundary == "run_cost"


@pytest.mark.asyncio
async def test_unknown_pricing_is_not_treated_as_zero(tmp_path) -> None:
    response = chat_response(Usage(input_tokens=1))
    budget = controller(
        tmp_path,
        configured_limits=limits(),
        catalog=prices(input_price=None),
    )
    provider = BudgetedChatModel(
        ScriptedChatModel([response, response], model="test-chat"),
        budget,
    )
    request = ChatRequest(messages=[Message(role=MessageRole.USER, content="loop")])

    await provider.chat(request)
    with pytest.raises(BudgetExceededError) as caught:
        await provider.chat(request)

    assert caught.value.boundary == "unknown_pricing"
    assert budget.state.known_cost_usd == 0
    assert budget.state.unknown_price_calls == 1


@pytest.mark.asyncio
async def test_rerank_boundary_stops_next_search(tmp_path) -> None:
    budget = controller(
        tmp_path,
        configured_limits=limits(max_rerank_searches_per_run=1),
    )
    provider = BudgetedRerankModel(
        ScriptedRerankModel(
            [
                ScriptedRerankStep(scores=(0.8,)),
                ScriptedRerankStep(scores=(0.8,)),
            ],
            model="test-rerank",
        ),
        budget,
    )
    request = RerankRequest(
        query="query",
        documents=[Document(id="doc", text="document")],
    )

    await provider.rerank(request)
    with pytest.raises(BudgetExceededError) as caught:
        await provider.rerank(request)

    assert caught.value.boundary == "rerank_searches"
