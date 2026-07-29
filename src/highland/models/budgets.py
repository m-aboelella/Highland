from __future__ import annotations

from collections.abc import AsyncIterator
from dataclasses import dataclass
from datetime import UTC, datetime

from highland.storage.cost_ledger import CostLedger, new_usage_entry

from .contracts import (
    ChatModel,
    ChatRequest,
    ChatResponse,
    ChatStreamEvent,
    EmbeddingModel,
    EmbeddingRequest,
    EmbeddingResponse,
    ModelError,
    RerankModel,
    RerankRequest,
    RerankResponse,
    ResponseMetadata,
    Usage,
)
from .pricing import PriceCatalog


class BudgetExceededError(ModelError):
    def __init__(self, boundary: str, message: str) -> None:
        self.boundary = boundary
        super().__init__(
            message,
            code="budget_exceeded",
            retryable=False,
            provider="highland",
        )


@dataclass(frozen=True, slots=True)
class BudgetLimits:
    max_model_calls_per_run: int
    max_rerank_searches_per_run: int
    max_tokens_per_run: int
    max_run_cost_usd: float
    monthly_budget_usd: float


@dataclass(slots=True)
class BudgetState:
    calls: int = 0
    rerank_searches: int = 0
    tokens: int = 0
    known_cost_usd: float = 0
    unknown_price_calls: int = 0


class BudgetController:
    def __init__(
        self,
        *,
        run_id: str,
        limits: BudgetLimits,
        prices: PriceCatalog,
        ledger: CostLedger,
    ) -> None:
        self.run_id = run_id
        self.limits = limits
        self.prices = prices
        self.ledger = ledger
        self.state = BudgetState()

    def before_call(self, operation: str, model: str) -> None:
        checks = (
            (
                self.state.calls >= self.limits.max_model_calls_per_run,
                "model_calls",
                f"Run model-call limit reached ({self.limits.max_model_calls_per_run})",
            ),
            (
                operation == "rerank"
                and self.state.rerank_searches >= self.limits.max_rerank_searches_per_run,
                "rerank_searches",
                f"Run rerank limit reached ({self.limits.max_rerank_searches_per_run})",
            ),
            (
                self.state.tokens >= self.limits.max_tokens_per_run,
                "tokens",
                f"Run token limit reached ({self.limits.max_tokens_per_run})",
            ),
            (
                self.state.known_cost_usd >= self.limits.max_run_cost_usd,
                "run_cost",
                f"Run cost limit reached (${self.limits.max_run_cost_usd:.4f})",
            ),
        )
        for exceeded, boundary, message in checks:
            if exceeded:
                raise BudgetExceededError(boundary, message)
        if self.state.unknown_price_calls:
            raise BudgetExceededError(
                "unknown_pricing",
                "Cannot start another model call because a prior call has unknown pricing",
            )
        if self.prices.price_for(model) is None:
            raise BudgetExceededError(
                "unknown_pricing",
                f"No effective price configuration exists for model {model}",
            )
        monthly = self.ledger.summarize(month=datetime.now(UTC).date())
        if monthly.unknown_price_calls:
            raise BudgetExceededError(
                "unknown_pricing",
                "Cannot verify monthly budget because the ledger contains unknown pricing",
            )
        if monthly.known_cost_usd >= self.limits.monthly_budget_usd:
            raise BudgetExceededError(
                "monthly_cost",
                f"Calendar-month cost limit reached (${self.limits.monthly_budget_usd:.4f})",
            )

    def record(
        self,
        *,
        operation: str,
        model: str,
        usage: Usage,
        metadata: ResponseMetadata,
    ) -> None:
        estimated = self.prices.estimate(model, operation, usage)
        self.state.calls += 1
        self.state.rerank_searches += operation == "rerank"
        self.state.tokens += usage.total_tokens or 0
        if estimated is None:
            self.state.unknown_price_calls += 1
        else:
            self.state.known_cost_usd += estimated
        self.ledger.append(
            new_usage_entry(
                run_id=self.run_id,
                logical_call_id=metadata.logical_call_id,
                operation=operation,
                provider=metadata.provider,
                model=model,
                request_id=metadata.request_id,
                usage=usage,
                estimated_cost_usd=estimated,
                latency_ms=metadata.latency_ms,
            )
        )


class BudgetedChatModel:
    def __init__(self, delegate: ChatModel, controller: BudgetController) -> None:
        self.delegate = delegate
        self.controller = controller
        self.name = delegate.name
        self.capabilities = delegate.capabilities
        self.model = getattr(delegate, "model", delegate.name)

    async def chat(self, request: ChatRequest) -> ChatResponse:
        self.controller.before_call("chat", self.model)
        response = await self.delegate.chat(request)
        self.controller.record(
            operation="chat",
            model=self.model,
            usage=response.usage,
            metadata=response.metadata,
        )
        return response

    async def stream(self, request: ChatRequest) -> AsyncIterator[ChatStreamEvent]:
        self.controller.before_call("chat", self.model)
        recorded = False
        async for event in self.delegate.stream(request):
            if event.type == "message-end" and event.response is not None:
                self.controller.record(
                    operation="chat",
                    model=self.model,
                    usage=event.response.usage,
                    metadata=event.response.metadata,
                )
                recorded = True
            yield event
        if not recorded:
            raise ModelError(
                "Model stream ended without usage metadata",
                code="malformed_provider_response",
                provider=self.name,
            )


class BudgetedEmbeddingModel:
    def __init__(self, delegate: EmbeddingModel, controller: BudgetController) -> None:
        self.delegate = delegate
        self.controller = controller
        self.name = delegate.name
        self.model = getattr(delegate, "model", delegate.name)

    async def embed(self, request: EmbeddingRequest) -> EmbeddingResponse:
        self.controller.before_call("embed", self.model)
        response = await self.delegate.embed(request)
        self.controller.record(
            operation="embed",
            model=self.model,
            usage=response.usage,
            metadata=response.metadata,
        )
        return response


class BudgetedRerankModel:
    def __init__(self, delegate: RerankModel, controller: BudgetController) -> None:
        self.delegate = delegate
        self.controller = controller
        self.name = delegate.name
        self.model = getattr(delegate, "model", delegate.name)

    async def rerank(self, request: RerankRequest) -> RerankResponse:
        self.controller.before_call("rerank", self.model)
        response = await self.delegate.rerank(request)
        self.controller.record(
            operation="rerank",
            model=self.model,
            usage=response.usage,
            metadata=response.metadata,
        )
        return response
