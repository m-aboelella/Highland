from __future__ import annotations

import argparse
import json

import uvicorn

from .doctor import inspect_environment
from .settings import HighlandSettings
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
    else:
        raise AssertionError(f"Unhandled command: {args.command}")


if __name__ == "__main__":
    main()
