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
async def test_live_customer_meeting_briefing_is_structured_and_uncertain(tmp_path) -> None:
    provider = build_model_provider(
        HighlandSettings(model_backend=ModelBackend.COHERE, workspace_dir=tmp_path)
    )
    response = await provider.chat.chat(
        ChatRequest(
            messages=[
                Message(
                    role=MessageRole.USER,
                    content=(
                        "Prepare a Northwind meeting briefing with headings Deployment, "
                        "Recent issues, Unresolved actions, and Talking points. Cite supplied "
                        "evidence and state that the compaction cause is only a hypothesis."
                    ),
                )
            ],
            documents=[
                Document(
                    id="northwind-account",
                    text=(
                        "Northwind is a strategic private-cloud account running 4.18.2. "
                        "Renewal is October 31."
                    ),
                ),
                Document(
                    id="northwind-incident",
                    text=(
                        "Production retrieval p95 rose during compaction. Memory contention "
                        "is a hypothesis pending a controlled run, not a confirmed cause."
                    ),
                ),
                Document(
                    id="northwind-actions",
                    text="Capacity review and agreement on a latency SLO remain open.",
                ),
            ],
        )
    )
    lowered = response.message.content.lower()
    assert all(
        heading in lowered
        for heading in ("deployment", "recent issues", "unresolved actions", "talking points")
    )
    assert "hypothesis" in lowered or "not confirmed" in lowered
    assert response.citations
