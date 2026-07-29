from __future__ import annotations

from dataclasses import dataclass

import cohere

from highland.settings import HighlandSettings, ModelBackend
from highland.storage.cost_ledger import CostLedger
from highland.workspace import WorkspacePaths

from .budgets import (
    BudgetController,
    BudgetedChatModel,
    BudgetedEmbeddingModel,
    BudgetedRerankModel,
    BudgetLimits,
)
from .cohere import CohereChatModel, CohereEmbeddingModel, CohereRerankModel
from .contracts import ChatModel, EmbeddingModel, RerankModel
from .pricing import PriceCatalog
from .scripted import DeterministicEmbeddingModel, DeterministicRerankModel, ScriptedChatModel


@dataclass(frozen=True, slots=True)
class ModelProvider:
    chat: ChatModel
    embeddings: EmbeddingModel
    rerank: RerankModel


def build_model_provider(settings: HighlandSettings) -> ModelProvider:
    """Construct only the explicitly selected backend."""
    if settings.model_backend is ModelBackend.SCRIPTED:
        return ModelProvider(
            chat=ScriptedChatModel([], model="scripted-chat"),
            embeddings=DeterministicEmbeddingModel(model="deterministic-embedding"),
            rerank=DeterministicRerankModel(),
        )
    if settings.cohere_api_key is None:
        raise ValueError("COHERE_API_KEY is required when HIGHLAND_MODEL_BACKEND=cohere")
    client = cohere.AsyncClientV2(
        api_key=settings.cohere_api_key.get_secret_value(),
        timeout=settings.request_timeout_seconds,
        max_retries=0,
        client_name="highland-educational",
    )
    return ModelProvider(
        chat=CohereChatModel(client, model=settings.chat_model),
        embeddings=CohereEmbeddingModel(client, model=settings.embedding_model),
        rerank=CohereRerankModel(client, model=settings.rerank_model),
    )


def apply_model_budgets(
    provider: ModelProvider,
    settings: HighlandSettings,
    *,
    run_id: str,
) -> ModelProvider:
    """Attach one shared, fail-closed budget and ledger to a model run."""
    workspace = WorkspacePaths.from_root(settings.workspace_dir)
    workspace.ensure()
    controller = BudgetController(
        run_id=run_id,
        limits=BudgetLimits(
            max_model_calls_per_run=settings.max_model_calls_per_run,
            max_rerank_searches_per_run=settings.max_rerank_searches_per_run,
            max_tokens_per_run=settings.max_tokens_per_run,
            max_run_cost_usd=settings.max_run_cost_usd,
            monthly_budget_usd=settings.monthly_budget_usd,
        ),
        prices=PriceCatalog.load(settings.model_price_config),
        ledger=CostLedger(workspace.runs / "cost-ledger.jsonl"),
    )
    return ModelProvider(
        chat=BudgetedChatModel(provider.chat, controller),
        embeddings=BudgetedEmbeddingModel(provider.embeddings, controller),
        rerank=BudgetedRerankModel(provider.rerank, controller),
    )
