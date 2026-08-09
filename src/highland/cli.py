from __future__ import annotations

import argparse
from pathlib import Path

from .cli_commands import _retrieval_baseline_path, run_command
from .retrieval.sources import INDEXABLE_SOURCES
from .settings import HighlandSettings

__all__ = ["_retrieval_baseline_path", "build_parser", "main"]


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
    reset = subparsers.add_parser(
        "reset",
        help="Restore mock source state and clear Highland platform state.",
    )
    reset.add_argument("--yes", action="store_true", help="Confirm the displayed reset targets.")
    backup = subparsers.add_parser("backup", help="Export or import human-created local state.")
    backup_commands = backup.add_subparsers(dest="backup_command", required=True)
    backup_export = backup_commands.add_parser("export", help="Create a portable ZIP backup.")
    backup_export.add_argument("path", type=Path)
    backup_import = backup_commands.add_parser("import", help="Restore a portable ZIP backup.")
    backup_import.add_argument("path", type=Path)
    backup_import.add_argument(
        "--replace",
        action="store_true",
        help="Replace non-empty artifact, workflow, and trace targets.",
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
    retrieval = evaluation_commands.add_parser(
        "retrieval", help="Run deterministic retrieval and isolation evaluation."
    )
    retrieval.add_argument("--baseline", type=Path, help="Stored retrieval report to compare.")
    retrieval.add_argument(
        "--enforce-baseline",
        action="store_true",
        help="Fail when a quality metric falls below the baseline.",
    )
    scenario = evaluation_commands.add_parser(
        "scenario", help="Run one explicitly opted-in Cohere scenario evaluation."
    )
    scenario.add_argument("scenario_id", help="Scenario filename without .json or manifest ID.")
    scenario.add_argument("--baseline", type=Path, help="Stored cost report to compare.")
    scenario.add_argument("--repeat", type=int, default=1, help="Number of independent runs.")
    all_scenarios = evaluation_commands.add_parser(
        "all", help="Run all explicitly opted-in Cohere scenario evaluations."
    )
    all_scenarios.add_argument("--baseline", type=Path, help="Stored cost report to compare.")
    all_scenarios.add_argument("--repeat", type=int, default=1, help="Runs per scenario.")
    return parser



def main() -> None:
    run_command(HighlandSettings(), build_parser().parse_args())


if __name__ == "__main__":
    main()
