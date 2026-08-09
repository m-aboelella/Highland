"""Command handlers for the Highland CLI transport."""

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
from .maintenance import (
    export_learning_state,
    import_learning_state,
    reset_local_state,
    reset_targets,
)
from .models.pricing import PriceCatalog
from .models.provider import build_model_provider
from .retrieval.ingestion import BackfillService
from .retrieval.sources import MCPSourceReader
from .retrieval.sync import IndexSynchronizer
from .services import ApplicationServices
from .settings import HighlandSettings
from .storage.cost_ledger import CostLedger, UsageSummary
from .workspace import WorkspacePaths


def run_command(settings: HighlandSettings, args: argparse.Namespace) -> None:
    """Dispatch parsed arguments to one focused command handler."""
    command = args.command
    if command == "app":
        run_app(settings)
    elif command == "doctor":
        run_doctor(settings, as_json=args.json)
    elif command == "reset-platform-state":
        reset_platform_state(settings)
    elif command == "reset":
        reset_all_state(settings, confirmed=args.yes)
    elif command == "backup":
        run_backup(settings, args)
    elif command == "usage":
        show_usage(settings, run_id=args.run_id)
    elif command == "index":
        run_index(settings, args)
    elif command == "eval" and args.evaluation_command == "retrieval":
        run_retrieval_evaluation(settings, args)
    elif command == "eval" and args.evaluation_command in {"scenario", "all"}:
        run_scenario_evaluation(settings, args)
    else:
        raise AssertionError(f"Unhandled command: {command}")


def run_app(settings: HighlandSettings) -> None:
    uvicorn.run(
        "highland.api:create_app",
        factory=True,
        host=settings.host,
        port=settings.port,
    )


def run_doctor(settings: HighlandSettings, *, as_json: bool) -> None:
    report = inspect_environment(settings)
    if as_json:
        print(json.dumps(report, indent=2))
    else:
        _print_doctor(report)


def reset_platform_state(settings: HighlandSettings) -> None:
    workspace = WorkspacePaths.from_root(settings.workspace_dir)
    workspace.reset()
    print(f"Reset Highland platform state in {workspace.root}")


def reset_all_state(settings: HighlandSettings, *, confirmed: bool) -> None:
    targets = reset_targets(settings)
    print("Reset will restore or clear only these targets:")
    for target in targets:
        print(f"  {target}")
    if not confirmed and input("Type 'reset' to continue: ").strip() != "reset":
        raise SystemExit("Reset cancelled.")
    reset_local_state(settings)
    print("Restored mock source state and cleared Highland platform state.")


def run_backup(settings: HighlandSettings, args: argparse.Namespace) -> None:
    if args.backup_command == "export":
        destination = export_learning_state(settings.workspace_dir, args.path)
        print(f"Exported artifacts, workflows, and traces to {destination}")
        return
    targets = import_learning_state(
        settings.workspace_dir,
        args.path,
        replace=args.replace,
    )
    print("Imported artifacts, workflows, and traces into:")
    for target in targets:
        print(f"  {target}")


def show_usage(settings: HighlandSettings, *, run_id: str | None) -> None:
    workspace = WorkspacePaths.from_root(settings.workspace_dir)
    ledger = CostLedger(workspace.runs / "cost-ledger.jsonl")
    selected_run_id = run_id or ledger.latest_run_id()
    run = ledger.summarize(run_id=selected_run_id or "")
    today = datetime.now(UTC).date()
    month = ledger.summarize(month=today)
    print(f"Current run: {selected_run_id or 'none'}")
    _print_usage(run)
    print(f"Calendar month: {today:%Y-%m}")
    _print_usage(month)


def run_index(settings: HighlandSettings, args: argparse.Namespace) -> None:
    workspace = WorkspacePaths.from_root(settings.workspace_dir)
    workspace.ensure()
    reader = MCPSourceReader(
        settings.connector_commands,
        timeout_seconds=settings.connector_timeout_seconds,
    )
    if args.index_command == "backfill":
        service = BackfillService(
            reader,
            index_dir=workspace.indexes / "search",
            reports_dir=workspace.synchronization,
            embedding_model=build_model_provider(settings).embeddings,
        )
        backfill_result = asyncio.run(service.backfill(None if args.all else args.source))
        print(backfill_result.model_dump_json(indent=2))
        if not backfill_result.promoted:
            raise SystemExit(1)
        return
    else:
        synchronizer = IndexSynchronizer(
            reader,
            index_dir=workspace.indexes / "search",
            reports_dir=workspace.synchronization,
            embedding_model=build_model_provider(settings).embeddings,
        )
        if args.index_command == "status":
            manifest = synchronizer.status()
            print(manifest.model_dump_json(indent=2) if manifest else '{"state":"missing"}')
            return
        operation = synchronizer.sync if args.index_command == "sync" else synchronizer.rebuild
        sync_result = asyncio.run(operation())
    print(sync_result.model_dump_json(indent=2))
    if not sync_result.promoted:
        raise SystemExit(1)


def run_retrieval_evaluation(
    settings: HighlandSettings,
    args: argparse.Namespace,
) -> None:
    if settings.model_backend.value == "cohere" and (
        os.getenv("HIGHLAND_RUN_LIVE_TESTS") != "1" or settings.cohere_api_key is None
    ):
        raise SystemExit(
            "live retrieval evaluation requires HIGHLAND_RUN_LIVE_TESTS=1 and COHERE_API_KEY"
        )
    services = ApplicationServices.build(settings)
    baseline = _retrieval_baseline_path(
        settings.retrieval_baseline_config,
        args.baseline,
        enforce=args.enforce_baseline,
    )
    report = asyncio.run(
        evaluate_retrieval(
            services.discover,
            relevance_path=settings.retrieval_relevance_config,
            index_dir=services.workspace.indexes / "search",
            reports_dir=settings.workspace_dir / "reports" / "retrieval",
            backend=settings.model_backend.value,
            model_ids=services.effective_models(),
            baseline_path=baseline,
            enforce_baseline=args.enforce_baseline,
        )
    )
    print(
        f"backend={report.provenance.backend} candidate_recall={report.candidate_recall:.1%} "
        f"precision_at_k={report.precision_at_k:.1%} "
        f"recall_at_k={report.recall_at_k:.1%} mrr_at_k={report.mrr_at_k:.3f} "
        f"result={'PASS' if report.passed else 'FAIL'}"
    )
    for case in report.cases:
        if not case.passed:
            print(
                f"FAIL {case.case_id}: stage={case.failure_stage} "
                f"missing={case.missing_top_k_ids} leakage={case.leaked_source_ids}"
            )
    print(f"reports={settings.workspace_dir / 'reports' / 'retrieval'}")
    if not report.passed:
        raise SystemExit(1)


def run_scenario_evaluation(
    settings: HighlandSettings,
    args: argparse.Namespace,
) -> None:
    if os.getenv("HIGHLAND_RUN_LIVE_TESTS") != "1" or settings.cohere_api_key is None:
        raise SystemExit(
            "live model evaluation requires HIGHLAND_RUN_LIVE_TESTS=1 and COHERE_API_KEY"
        )
    paths = _scenario_paths(args)
    costs = EvaluationCostTracker(
        PriceCatalog.load(settings.model_price_config),
        warning_budget_usd=settings.evaluation_warning_budget_usd,
        hard_budget_usd=settings.evaluation_hard_budget_usd,
    )
    services = ApplicationServices.build(settings)
    evaluator = LiveScenarioEvaluator(
        services,
        reports_dir=settings.workspace_dir / "reports" / "scenarios",
        costs=costs,
    )
    evaluations = [
        asyncio.run(evaluator.evaluate(path, repeat=args.repeat)) for path in paths
    ]
    for evaluation in evaluations:
        print(
            f"model-backed scenario={evaluation.scenario_id} "
            f"repeat={evaluation.repeat} pass_rate={evaluation.pass_rate:.1%} "
            f"result={'PASS' if evaluation.passed else 'FAIL'}"
        )
    cost_report = costs.write(
        settings.workspace_dir / "reports" / "scenarios" / "cost-report.json",
        baseline=args.baseline,
    )
    print(
        f"evaluation_cost=${cost_report.known_cost_usd:.6f} "
        f"unknown_price_calls={cost_report.unknown_price_calls}"
    )
    if not all(evaluation.passed for evaluation in evaluations):
        raise SystemExit(1)


def _scenario_paths(args: argparse.Namespace) -> list[Path]:
    scenarios_dir = Path(__file__).resolve().parents[2] / "data" / "scenarios"
    if args.evaluation_command == "all":
        return sorted(scenarios_dir.glob("*.json"))
    requested = args.scenario_id
    paths = [
        path
        for path in scenarios_dir.glob("*.json")
        if path.stem == requested
        or json.loads(path.read_text(encoding="utf-8"))["id"] == requested
    ]
    if not paths:
        raise SystemExit(f"unknown scenario: {requested}")
    return paths


def _retrieval_baseline_path(
    configured: Path,
    requested: Path | None,
    *,
    enforce: bool,
) -> Path | None:
    if requested is not None:
        return requested
    return configured if enforce else None


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


def _print_usage(summary: UsageSummary) -> None:
    print(
        f"  calls={summary.calls} "
        f"tokens={summary.input_tokens + summary.output_tokens} "
        f"search_units={summary.search_units:g} "
        f"known_cost_usd=${summary.known_cost_usd:.6f} "
        f"unknown_price_calls={summary.unknown_price_calls}"
    )
