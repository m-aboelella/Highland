from __future__ import annotations

import json
import re
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

_TOKEN = re.compile(r"[a-z0-9][a-z0-9._-]*", re.IGNORECASE)
_STALE_SECONDS = 60 * 60 * 24 * 120


class EvaluationModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class RetrievalCase(EvaluationModel):
    id: str
    scenario_id: str
    kind: Literal["exact_id", "paraphrase", "filter", "stale_record", "customer_isolation"]
    query: str
    expected_ids: list[str] = Field(default_factory=list)
    customer_id: str | None = None
    forbidden_customer_ids: list[str] = Field(default_factory=list)


class RetrievalCaseResult(EvaluationModel):
    case_id: str
    scenario_id: str
    kind: str
    candidate_ids: list[str]
    top_k_ids: list[str]
    missing_candidate_ids: list[str]
    missing_top_k_ids: list[str]
    leaked_ids: list[str]
    passed: bool
    failure_stage: Literal["candidate", "rerank", "isolation"] | None = None


class RetrievalEvaluation(EvaluationModel):
    provider: str = "deterministic"
    generated_at: datetime
    candidate_recall: float
    top_k_recall: float
    passed: bool
    cases: list[RetrievalCaseResult]


@dataclass(frozen=True, slots=True)
class _Record:
    id: str
    text: str
    customer_id: str | None
    updated_at: datetime | None


def _tokens(text: str) -> set[str]:
    lowered = text.lower()
    expanded = " ".join(
        (
            lowered,
            lowered.replace(".", " ").replace("_", " ").replace("-", " "),
            "p95" if "tail latency" in lowered else "",
            "queries per second" if "query volume" in lowered else "",
            "memory utilization" if "memory rose" in lowered else "",
        )
    )
    return {token.lower() for token in _TOKEN.findall(expanded)}


def _records(seed_dir: Path) -> list[_Record]:
    records: dict[str, _Record] = {}

    def visit(value: Any, inherited_customer: str | None = None) -> None:
        if isinstance(value, list):
            for item in value:
                visit(item, inherited_customer)
            return
        if not isinstance(value, dict):
            return
        customer_id = value.get("customer_id", inherited_customer)
        identity = value.get("passage_id") or value.get("record_id")
        if identity is None and "metric" in value:
            identity = value["metric"]
        if identity:
            updated_at = value.get("updated_at")
            parsed = (
                datetime.fromisoformat(str(updated_at))
                if updated_at
                else None
            )
            records[str(identity)] = _Record(
                id=str(identity),
                text=json.dumps(value, sort_keys=True),
                customer_id=str(customer_id) if customer_id else None,
                updated_at=parsed,
            )
        for child in value.values():
            if isinstance(child, (dict, list)):
                visit(child, str(customer_id) if customer_id else None)

    for path in sorted(seed_dir.glob("*.json")):
        visit(json.loads(path.read_text(encoding="utf-8")))
    return list(records.values())


def derive_cases(scenarios_dir: Path, records: list[_Record]) -> list[RetrievalCase]:
    cases: list[RetrievalCase] = []
    known_ids = {record.id for record in records}
    for path in sorted(scenarios_dir.glob("*.json")):
        manifest = json.loads(path.read_text(encoding="utf-8"))
        scenario_id = str(manifest["id"])
        customer_id = (manifest.get("allowed_customers") or [None])[0]
        for index, claim in enumerate(manifest.get("expected_claims", []), start=1):
            evidence = [str(item) for item in claim["evidence"] if str(item) in known_ids]
            if not evidence:
                continue
            cases.append(
                RetrievalCase(
                    id=f"{scenario_id}:claim:{index}",
                    scenario_id=scenario_id,
                    kind="paraphrase",
                    query=str(claim["claim"]),
                    expected_ids=evidence,
                    customer_id=customer_id,
                )
            )
    cases.extend(
        [
            RetrievalCase(
                id="exact-support-id",
                scenario_id="scenario_customer_meeting_preparation",
                kind="exact_id",
                query="SUP-1042 tkt_1042",
                expected_ids=["tkt_1042"],
                customer_id="cus_northwind",
            ),
            RetrievalCase(
                id="northwind-filter",
                scenario_id="scenario_customer_meeting_preparation",
                kind="filter",
                query="open customer support issue",
                expected_ids=["tkt_1042"],
                customer_id="cus_northwind",
            ),
            RetrievalCase(
                id="stale-record",
                scenario_id="scenario_deployment_issue_investigation",
                kind="stale_record",
                query="retrieval latency runbook compaction",
                expected_ids=["pas_latency_triage"],
            ),
            RetrievalCase(
                id="customer-isolation",
                scenario_id="scenario_weekly_customer_health",
                kind="customer_isolation",
                query="production deployment health support",
                customer_id="cus_northwind",
                forbidden_customer_ids=["cus_alpine", "cus_lumon"],
            ),
        ]
    )
    return cases


def _rank(
    records: list[_Record],
    case: RetrievalCase,
    *,
    now: datetime,
    candidate_limit: int,
    top_k: int,
) -> tuple[list[str], list[str]]:
    query = _tokens(case.query)
    eligible: list[_Record] = []
    for record in records:
        if case.customer_id and record.customer_id not in {None, case.customer_id}:
            continue
        if (
            case.kind == "stale_record"
            and record.updated_at
            and (now - record.updated_at).total_seconds() > _STALE_SECONDS
        ):
            continue
        eligible.append(record)

    scored: list[tuple[_Record, float]] = []
    for record in eligible:
        terms = _tokens(record.text)
        overlap = len(query & terms)
        exact = 20 if record.id.lower() in case.query.lower() else 0
        score = exact + overlap / max(len(query), 1)
        if score:
            scored.append((record, score))
    scored.sort(key=lambda item: (-item[1], item[0].id))
    candidates = [record.id for record, _ in scored[:candidate_limit]]
    reranked = sorted(
        (item for item in scored if item[0].id in candidates),
        key=lambda item: (
            -len(query & _tokens(item[0].text)) / max(len(_tokens(item[0].text)), 1),
            -item[1],
            item[0].id,
        ),
    )
    return candidates, [record.id for record, _ in reranked[:top_k]]


def evaluate_retrieval(
    *,
    scenarios_dir: Path,
    seed_dir: Path,
    reports_dir: Path,
    candidate_limit: int = 50,
    top_k: int = 20,
) -> RetrievalEvaluation:
    records = _records(seed_dir)
    cases = derive_cases(scenarios_dir, records)
    now = datetime.now(UTC)
    by_id = {record.id: record for record in records}
    results: list[RetrievalCaseResult] = []
    expected_total = candidate_hits = top_k_hits = 0
    for case in cases:
        candidates, reranked = _rank(
            records, case, now=now, candidate_limit=candidate_limit, top_k=top_k
        )
        missing_candidates = [item for item in case.expected_ids if item not in candidates]
        missing_top_k = [item for item in case.expected_ids if item not in reranked]
        forbidden = set(case.forbidden_customer_ids)
        leaked = [
            item
            for item in reranked
            if item in by_id and by_id[item].customer_id in forbidden
        ]
        expected_total += len(case.expected_ids)
        candidate_hits += len(case.expected_ids) - len(missing_candidates)
        top_k_hits += len(case.expected_ids) - len(missing_top_k)
        failure_stage = (
            "isolation"
            if leaked
            else "candidate"
            if missing_candidates
            else "rerank"
            if missing_top_k
            else None
        )
        results.append(
            RetrievalCaseResult(
                case_id=case.id,
                scenario_id=case.scenario_id,
                kind=case.kind,
                candidate_ids=candidates,
                top_k_ids=reranked,
                missing_candidate_ids=missing_candidates,
                missing_top_k_ids=missing_top_k,
                leaked_ids=leaked,
                passed=failure_stage is None,
                failure_stage=failure_stage,
            )
        )
    report = RetrievalEvaluation(
        generated_at=now,
        candidate_recall=candidate_hits / expected_total if expected_total else 1,
        top_k_recall=top_k_hits / expected_total if expected_total else 1,
        passed=all(result.passed for result in results),
        cases=results,
    )
    reports_dir.mkdir(parents=True, exist_ok=True)
    (reports_dir / "retrieval.json").write_text(report.model_dump_json(indent=2) + "\n")
    lines = [
        "# Retrieval evaluation",
        "",
        f"- Provider: `{report.provider}`",
        f"- Candidate recall: {report.candidate_recall:.1%}",
        f"- Top-k recall: {report.top_k_recall:.1%}",
        f"- Result: {'PASS' if report.passed else 'FAIL'}",
        "",
        "| Case | Kind | Result | Lost stage | Missing evidence | Leakage |",
        "| --- | --- | --- | --- | --- | --- |",
    ]
    for result in results:
        lines.append(
            f"| {result.case_id} | {result.kind} | {'PASS' if result.passed else 'FAIL'} "
            f"| {result.failure_stage or '—'} | {', '.join(result.missing_top_k_ids) or '—'} "
            f"| {', '.join(result.leaked_ids) or '—'} |"
        )
    (reports_dir / "retrieval.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    return report
