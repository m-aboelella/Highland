from __future__ import annotations

import os

import pytest

from highland.models.contracts import (
    ChatRequest,
    Document,
    EmbeddingRequest,
    InputType,
    Message,
    MessageRole,
    RerankRequest,
)
from highland.models.provider import build_model_provider
from highland.settings import HighlandSettings, ModelBackend

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(
        os.getenv("HIGHLAND_RUN_LIVE_TESTS") != "1" or not os.getenv("COHERE_API_KEY"),
        reason="requires HIGHLAND_RUN_LIVE_TESTS=1 and COHERE_API_KEY",
    ),
]


@pytest.mark.asyncio
async def test_live_cohere_chat_embed_and_rerank_smoke() -> None:
    provider = build_model_provider(HighlandSettings(model_backend=ModelBackend.COHERE))

    chat = await provider.chat.chat(
        ChatRequest(messages=[Message(role=MessageRole.USER, content="Reply with only: highland")])
    )
    embedding = await provider.embeddings.embed(
        EmbeddingRequest(texts=["Highland smoke test"], input_type=InputType.SEARCH_DOCUMENT)
    )
    rerank = await provider.rerank.rerank(
        RerankRequest(
            query="enterprise search",
            documents=[
                Document(id="relevant", text="Enterprise semantic search"),
                Document(id="other", text="A recipe for bread"),
            ],
            top_n=1,
        )
    )

    assert chat.message.content
    assert embedding.vectors and embedding.vectors[0]
    assert rerank.results and rerank.results[0].document.id == "relevant"
