from __future__ import annotations

import argparse
import asyncio
import json
import os
from datetime import UTC, datetime
from pathlib import Path

import uvicorn

from .doctor import inspect_environment
from .evaluation.costs import EvaluationCostTracker
from .evaluation.retrieval import evaluate_retrieval
from .evaluation.scenarios import LiveScenarioEvaluator
from .models.pricing import PriceCatalog
from .models.provider import build_model_provider
from .retrieval.ingestion import BackfillService
from .retrieval.sources import INDEXABLE_SOURCES, MCPSourceReader
from .retrieval.sync import IndexSynchronizer
from .settings import HighlandSettings
from .storage.cost_ledger import CostLedger, UsageSummary
from .workspace import WorkspacePaths


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="highland", description="Run and inspect Highland.")
    subparsers = parser.add_subparsers(dest="command", required=True)
    subparsers.add_parser("app", help="Run the Highland application API.")
    doctor = subparsers.add_parser("doctor", help="Inspect local application readiness.")
    doctor.add_argument("--json", action="store_true", help="Emit machine-readable output.")
    subparsers.add_parser(
        "reset-platform-state",
        help="Reset Highland state without changing mock source-system state.",
    )
    usage = subparsers.add_parser("usage", help="Summarize model usage and estimated cost.")
    usage.add_argument("--run-id", help="Run to summarize; defaults to the latest ledger run.")
    index = subparsers.add_parser("index", help="Build and inspect the local retrieval index.")
    index_commands = index.add_subparsers(dest="index_command", required=True)
    backfill = index_commands.add_parser("backfill", help="Backfill searchable source content.")
    selection = backfill.add_mutually_exclusive_group(required=True)
    selection.add_argument("--source", choices=INDEXABLE_SOURCES, action="append")
    selection.add_argument("--all", action="store_true")
    index_commands.add_parser("sync", help="Synchronize new, changed, and deleted content.")
    index_commands.add_parser("status", help="Show the current synchronization manifest.")
    index_commands.add_parser("rebuild", help="Safely rebuild all searchable content.")
    evaluation = subparsers.add_parser("eval", help="Run Highland evaluations.")
    evaluation_commands = evaluation.add_subparsers(dest="evaluation_command", required=True)
    evaluation_commands.add_parser(
        "retrieval", help="Run deterministic retrieval and isolation evaluation."
    )
    scenario = evaluation_commands.add_parser(
        "scenario", help="Run one explicitly opted-in Cohere scenario evaluation."
    )
    scenario.add_argument("scenario_id", help="Scenario filename without .json or manifest ID.")
    scenario.add_argument("--baseline", type=Path, help="Stored cost report to compare.")
    all_scenarios = evaluation_commands.add_parser(
        "all", help="Run all explicitly opted-in Cohere scenario evaluations."
    )
    all_scenarios.add_argument("--baseline", type=Path, help="Stored cost report to compare.")
    return parser


def _print_doctor(report: dict[str, object]) -> None:
    workspace = report["workspace"]
    models = report["models"]
    mock_services = report["mock_services"]
    assert isinstance(workspace, dict)
    assert isinstance(models, dict)
    assert isinstance(mock_services, dict)
    print(f"Workspace: {workspace['path']} ({'ready' if workspace['ready'] else 'not ready'})")
    print(f"Model backend: {models['backend']}")
    print(f"Cohere API key configured: {'yes' if models['cohere_key_configured'] else 'no'}")
    print(
        "Mock catalog: "
        f"{'reachable' if mock_services['reachable'] else 'unreachable'} "
        f"({mock_services['catalog_url']})"
    )
    connectors = report["connectors"]
    assert isinstance(connectors, dict)
    for name, details in connectors.items():
        assert isinstance(details, dict)
        state = "found" if details["executable_found"] else "missing"
        print(f"Connector {name}: {state} ({' '.join(details['command'])})")


def main() -> None:
    args = build_parser().parse_args()
    settings = HighlandSettings()
    if args.command == "app":
        uvicorn.run(
            "highland.api:create_app",
            factory=True,
            host=settings.host,
            port=settings.port,
        )
    elif args.command == "doctor":
        report = inspect_environment(settings)
        if args.json:
            print(json.dumps(report, indent=2))
        else:
            _print_doctor(report)
    elif args.command == "reset-platform-state":
        workspace = WorkspacePaths.from_root(settings.workspace_dir)
        workspace.reset()
        print(f"Reset Highland platform state in {workspace.root}")
    elif args.command == "usage":
        workspace = WorkspacePaths.from_root(settings.workspace_dir)
        ledger = CostLedger(workspace.runs / "cost-ledger.jsonl")
        run_id = args.run_id or ledger.latest_run_id()
        run = ledger.summarize(run_id=run_id) if run_id else ledger.summarize(run_id="")
        today = datetime.now(UTC).date()
        month = ledger.summarize(month=today)
        print(f"Current run: {run_id or 'none'}")
        _print_usage(run)
        print(f"Calendar month: {today:%Y-%m}")
        _print_usage(month)
    elif args.command == "index" and args.index_command == "backfill":
        workspace = WorkspacePaths.from_root(settings.workspace_dir)
        workspace.ensure()
        reader = MCPSourceReader(
            settings.connector_commands,
            timeout_seconds=settings.connector_timeout_seconds,
        )
        service = BackfillService(
            reader,
            index_dir=workspace.indexes / "search",
            reports_dir=workspace.synchronization,
            embedding_model=build_model_provider(settings).embeddings,
        )
        result = asyncio.run(service.backfill(None if args.all else args.source))
        print(result.model_dump_json(indent=2))
        if not result.promoted:
            raise SystemExit(1)
    elif args.command == "index":
        workspace = WorkspacePaths.from_root(settings.workspace_dir)
        workspace.ensure()
        reader = MCPSourceReader(
            settings.connector_commands,
            timeout_seconds=settings.connector_timeout_seconds,
        )
        synchronizer = IndexSynchronizer(
            reader,
            index_dir=workspace.indexes / "search",
            reports_dir=workspace.synchronization,
            embedding_model=build_model_provider(settings).embeddings,
        )
        if args.index_command == "status":
            manifest = synchronizer.status()
            print(manifest.model_dump_json(indent=2) if manifest else '{"state":"missing"}')
        else:
            operation = synchronizer.sync if args.index_command == "sync" else synchronizer.rebuild
            result = asyncio.run(operation())
            print(result.model_dump_json(indent=2))
            if not result.promoted:
                raise SystemExit(1)
    elif args.command == "eval" and args.evaluation_command == "retrieval":
        report = evaluate_retrieval(
            scenarios_dir=Path(__file__).resolve().parents[2] / "data" / "scenarios",
            seed_dir=Path(__file__).resolve().parents[2] / "data" / "seed",
            reports_dir=settings.workspace_dir / "reports" / "retrieval",
        )
        print(
            f"provider={report.provider} candidate_recall={report.candidate_recall:.1%} "
            f"top_k_recall={report.top_k_recall:.1%} "
            f"result={'PASS' if report.passed else 'FAIL'}"
        )
        for case in report.cases:
            if not case.passed:
                print(
                    f"FAIL {case.case_id}: stage={case.failure_stage} "
                    f"missing={case.missing_top_k_ids} leakage={case.leaked_ids}"
                )
        print(f"reports={settings.workspace_dir / 'reports' / 'retrieval'}")
        if not report.passed:
            raise SystemExit(1)
    elif args.command == "eval" and args.evaluation_command in {"scenario", "all"}:
        if (
            os.getenv("HIGHLAND_RUN_LIVE_TESTS") != "1"
            or settings.cohere_api_key is None
        ):
            raise SystemExit(
                "live model evaluation requires HIGHLAND_RUN_LIVE_TESTS=1 and COHERE_API_KEY"
            )
        scenarios_dir = Path(__file__).resolve().parents[2] / "data" / "scenarios"
        if args.evaluation_command == "scenario":
            requested = args.scenario_id
            paths = [
                path
                for path in scenarios_dir.glob("*.json")
                if path.stem == requested
                or json.loads(path.read_text(encoding="utf-8"))["id"] == requested
            ]
            if not paths:
                raise SystemExit(f"unknown scenario: {requested}")
        else:
            paths = sorted(scenarios_dir.glob("*.json"))
        costs = EvaluationCostTracker(
            PriceCatalog.load(settings.model_price_config),
            warning_budget_usd=settings.evaluation_warning_budget_usd,
            hard_budget_usd=settings.evaluation_hard_budget_usd,
        )
        evaluator = LiveScenarioEvaluator(
            build_model_provider(settings).chat,
            reports_dir=settings.workspace_dir / "reports" / "scenarios",
            seed_dir=Path(__file__).resolve().parents[2] / "data" / "seed",
            costs=costs,
        )
        grades = [asyncio.run(evaluator.evaluate(path)) for path in paths]
        for grade in grades:
            print(
                f"model-backed scenario={grade.scenario_id} "
                f"result={'PASS' if grade.passed else 'FAIL'}"
            )
        cost_report = costs.write(
            settings.workspace_dir / "reports" / "scenarios" / "cost-report.json",
            baseline=args.baseline,
        )
        print(
            f"evaluation_cost=${cost_report.known_cost_usd:.6f} "
            f"unknown_price_calls={cost_report.unknown_price_calls}"
        )
        if not all(grade.passed for grade in grades):
            raise SystemExit(1)
    else:
        raise AssertionError(f"Unhandled command: {args.command}")


def _print_usage(summary: UsageSummary) -> None:
    calls = summary.calls
    tokens = summary.input_tokens + summary.output_tokens
    searches = summary.search_units
    cost = summary.known_cost_usd
    unknown = summary.unknown_price_calls
    print(
        f"  calls={calls} tokens={tokens} search_units={searches:g} "
        f"known_cost_usd=${cost:.6f} unknown_price_calls={unknown}"
    )


if __name__ == "__main__":
    main()
