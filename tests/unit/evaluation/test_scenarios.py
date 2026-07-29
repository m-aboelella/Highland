from __future__ import annotations

import json
from pathlib import Path

import pytest

from highland.evaluation.scenarios import LiveScenarioEvaluator
from highland.models.contracts import (
    ChatResponse,
    Citation,
    FinishReason,
    Message,
    MessageRole,
    ResponseMetadata,
    Usage,
)
from highland.models.scripted import ScriptedChatModel


@pytest.mark.asyncio
async def test_scenario_report_separates_deterministic_and_judged_results(
    tmp_path: Path,
) -> None:
    seed = tmp_path / "seed"
    seed.mkdir()
    (seed / "records.json").write_text(
        json.dumps({"items": [{"record_id": "e1", "text": "supported fact"}]})
    )
    manifest = tmp_path / "scenario.json"
    manifest.write_text(
        json.dumps(
            {
                "id": "scenario_one",
                "prompt": "Explain the fact.",
                "required_tools": [],
                "expected_claims": [{"claim": "The fact is supported.", "evidence": ["e1"]}],
                "forbidden_behavior": ["Invent evidence."],
            }
        )
    )
    metadata = ResponseMetadata(provider="scripted", model="scripted", simulated=True)
    provider = ScriptedChatModel(
        [
            ChatResponse(
                message=Message(role=MessageRole.ASSISTANT, content="The fact is supported."),
                citations=[Citation(start=0, end=4, text="fact", source_ids=["e1"])],
                finish_reason=FinishReason.COMPLETE,
                usage=Usage(input_tokens=10, output_tokens=5),
                metadata=metadata,
            ),
            ChatResponse(
                message=Message(role=MessageRole.ASSISTANT, content="{}"),
                structured_output={
                    "claim_scores": [1.0],
                    "forbidden_behavior_score": 1.0,
                    "final_structure_score": 0.9,
                },
                finish_reason=FinishReason.COMPLETE,
                usage=Usage(input_tokens=5, output_tokens=2),
                metadata=metadata,
            ),
        ]
    )
    grade = await LiveScenarioEvaluator(
        provider, reports_dir=tmp_path / "reports", seed_dir=seed
    ).evaluate(manifest)
    assert grade.passed
    assert grade.usage.total_tokens == 22
    assert grade.semantic_scores["expected_claims"] == 1
    assert list((tmp_path / "reports" / "scenario_one").glob("*.json"))
