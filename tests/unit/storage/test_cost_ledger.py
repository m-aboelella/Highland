from __future__ import annotations

from datetime import UTC, datetime

from highland.models.contracts import Usage
from highland.storage.cost_ledger import CostLedger, UsageEntry


def entry(run_id: str = "run_1") -> UsageEntry:
    return UsageEntry(
        timestamp=datetime(2026, 7, 29, tzinfo=UTC),
        run_id=run_id,
        logical_call_id="logical_1",
        operation="chat",
        provider="cohere",
        model="command-a-plus-05-2026",
        request_id="request_1",
        usage=Usage(input_tokens=10, output_tokens=5),
        estimated_cost_usd=0.001,
        pricing_known=True,
        latency_ms=12,
    )


def test_ledger_is_append_only_and_contains_no_prompt_or_key(tmp_path) -> None:
    ledger = CostLedger(tmp_path / "runs" / "cost-ledger.jsonl")

    ledger.append(entry())
    ledger.append(entry("run_2"))

    raw = ledger.path.read_text(encoding="utf-8")
    assert len(raw.splitlines()) == 2
    assert "sensitive prompt" not in raw
    assert "secret-api-key" not in raw
    assert [item.run_id for item in ledger.entries()] == ["run_1", "run_2"]


def test_ledger_tolerates_a_truncated_final_record(tmp_path) -> None:
    ledger = CostLedger(tmp_path / "ledger.jsonl")
    ledger.append(entry())
    with ledger.path.open("a", encoding="utf-8") as handle:
        handle.write('{"timestamp":"truncated')

    assert ledger.entries() == [entry()]


def test_summaries_cover_run_and_calendar_month(tmp_path) -> None:
    ledger = CostLedger(tmp_path / "ledger.jsonl")
    ledger.append(entry())
    ledger.append(entry("run_2"))

    run = ledger.summarize(run_id="run_1")
    month = ledger.summarize(month=datetime(2026, 7, 1, tzinfo=UTC).date())

    assert run.calls == 1
    assert run.input_tokens == 10
    assert run.known_cost_usd == 0.001
    assert month.calls == 2
    assert month.output_tokens == 10
