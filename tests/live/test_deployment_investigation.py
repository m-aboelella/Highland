from __future__ import annotations

import os

import pytest

from highland.models.contracts import ChatRequest, Document, Message, MessageRole
from highland.models.provider import build_model_provider
from highland.settings import HighlandSettings, ModelBackend


@pytest.mark.live_scenario
@pytest.mark.skipif(
    not os.getenv("HIGHLAND_RUN_LIVE_TESTS") or not os.getenv("COHERE_API_KEY"),
    reason="live Cohere scenario is opt-in",
)
@pytest.mark.asyncio
async def test_live_model_keeps_compaction_hypothesis_uncertain() -> None:
    provider = build_model_provider(
        HighlandSettings(model_backend=ModelBackend.COHERE, workspace_dir="/tmp/highland-live")
    )
    response = await provider.chat.chat(
        ChatRequest(
            messages=[
                Message(
                    role=MessageRole.USER,
                    content=(
                        "Summarize the evidence. State explicitly that compaction is suspected "
                        "but not proven, and cite the supplied documents."
                    ),
                )
            ],
            documents=[
                Document(
                    id="runbook",
                    text=(
                        "Inspect compaction and cache eviction when query volume is stable and "
                        "one shard exceeds 85% memory."
                    ),
                ),
                Document(
                    id="metrics",
                    text=(
                        "Query volume stayed near baseline while p95 and shard-3 memory rose. "
                        "p95 declined after compaction was paused."
                    ),
                ),
            ],
        )
    )
    lowered = response.message.content.lower()
    assert "compaction" in lowered
    assert any(word in lowered for word in ("suspect", "hypothesis", "not proven", "uncertain"))
    assert response.citations
