from __future__ import annotations

import json
from pathlib import Path

from highland.evaluation.retrieval import evaluate_retrieval


def _write(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value), encoding="utf-8")


def test_retrieval_reports_stage_loss_and_writes_both_formats(tmp_path: Path) -> None:
    seed = tmp_path / "seed"
    scenarios = tmp_path / "scenarios"
    reports = tmp_path / "reports"
    _write(
        seed / "support.json",
        {"tickets": [{"record_id": "tkt_1", "customer_id": "cus_a", "text": "latency"}]},
    )
    _write(
        scenarios / "one.json",
        {
            "id": "one",
            "expected_claims": [{"claim": "unrelated wording", "evidence": ["tkt_1"]}],
        },
    )
    report = evaluate_retrieval(
        scenarios_dir=scenarios, seed_dir=seed, reports_dir=reports, top_k=1
    )
    assert not report.passed
    assert report.cases[0].failure_stage == "candidate"
    assert report.cases[0].missing_candidate_ids == ["tkt_1"]
    assert (reports / "retrieval.json").exists()
    assert "Missing evidence" in (reports / "retrieval.md").read_text()


def test_customer_isolation_is_a_hard_failure(tmp_path: Path) -> None:
    seed = tmp_path / "seed"
    scenarios = tmp_path / "scenarios"
    _write(
        seed / "records.json",
        {
            "items": [
                {"record_id": "safe", "customer_id": "cus_a", "text": "deployment health"},
                {"record_id": "foreign", "customer_id": "cus_b", "text": "deployment health"},
            ]
        },
    )
    _write(scenarios / "none.json", {"id": "none"})
    report = evaluate_retrieval(
        scenarios_dir=scenarios, seed_dir=seed, reports_dir=tmp_path / "reports"
    )
    isolation = next(case for case in report.cases if case.kind == "customer_isolation")
    assert isolation.leaked_ids == []
    assert isolation.passed
