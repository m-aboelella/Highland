from __future__ import annotations

from dataclasses import dataclass

import cohere

from highland.settings import HighlandSettings, ModelBackend

from .cohere import CohereChatModel, CohereEmbeddingModel, CohereRerankModel
from .contracts import ChatModel, EmbeddingModel, RerankModel
from .scripted import DeterministicEmbeddingModel, ScriptedChatModel, ScriptedRerankModel


@dataclass(frozen=True, slots=True)
class ModelProvider:
    chat: ChatModel
    embeddings: EmbeddingModel
    rerank: RerankModel


def build_model_provider(settings: HighlandSettings) -> ModelProvider:
    """Construct only the explicitly selected backend."""
    if settings.model_backend is ModelBackend.SCRIPTED:
        return ModelProvider(
            chat=ScriptedChatModel([], model=settings.chat_model),
            embeddings=DeterministicEmbeddingModel(model=settings.embedding_model),
            rerank=ScriptedRerankModel([], model=settings.rerank_model),
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
