from __future__ import annotations

import argparse
import multiprocessing
import os
import signal
from pathlib import Path

import uvicorn

from .api import create_app
from .constants import DEFAULT_PORTS, DEFAULT_RUNTIME_DIR, DEFAULT_SEED_DIR, SERVICES
from .seed import generate_seed
from .store import reset_all


def _serve(service: str, host: str, port: int, log_level: str = "info") -> None:
    uvicorn.run(create_app(service), host=host, port=port, log_level=log_level)


def _dev(host: str) -> None:
    if not DEFAULT_SEED_DIR.exists():
        generate_seed()
    processes: list[multiprocessing.Process] = []
    for service in ("catalog", *SERVICES):
        process = multiprocessing.Process(
            target=_serve,
            args=(service, host, DEFAULT_PORTS[service], "warning"),
            name=f"highland-{service}",
        )
        process.start()
        processes.append(process)

    print("Highland mock enterprise is running:")
    print(f"  catalog        http://localhost:{DEFAULT_PORTS['catalog']}")
    for service in SERVICES:
        print(f"  {service:<14} http://localhost:{DEFAULT_PORTS[service]}/docs")
    print("Press Ctrl-C to stop all services.")

    stopped = False

    def stop_all(_signum: int | None = None, _frame: object | None = None) -> None:
        nonlocal stopped
        if stopped:
            return
        stopped = True
        for process in processes:
            if process.is_alive():
                process.terminate()
        for process in processes:
            process.join(timeout=5)

    signal.signal(signal.SIGTERM, stop_all)
    signal.signal(signal.SIGINT, stop_all)
    try:
        for process in processes:
            process.join()
    finally:
        stop_all()


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="highland-mocks",
        description="Generate and run Highland's local synthetic enterprise systems.",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    generate = subparsers.add_parser("generate", help="Generate deterministic seed files.")
    generate.add_argument(
        "--output",
        type=Path,
        default=Path(os.getenv("HIGHLAND_SEED_DIR", str(DEFAULT_SEED_DIR))),
    )

    reset = subparsers.add_parser("reset", help="Restore runtime data from the seed.")
    reset.add_argument(
        "--seed-dir",
        type=Path,
        default=Path(os.getenv("HIGHLAND_SEED_DIR", str(DEFAULT_SEED_DIR))),
    )
    reset.add_argument(
        "--runtime-dir",
        type=Path,
        default=Path(os.getenv("HIGHLAND_RUNTIME_DIR", str(DEFAULT_RUNTIME_DIR))),
    )

    serve = subparsers.add_parser("serve", help="Run one mock REST service.")
    serve.add_argument("service", choices=("catalog", *SERVICES))
    serve.add_argument("--host", default="127.0.0.1")
    serve.add_argument("--port", type=int)
    serve.add_argument("--log-level", default="info")

    dev = subparsers.add_parser("dev", help="Run the catalog and all mock services.")
    dev.add_argument("--host", default="127.0.0.1")

    return parser


def main() -> None:
    args = build_parser().parse_args()
    if args.command == "generate":
        paths = generate_seed(args.output)
        print(f"Generated {len(paths)} seed files in {args.output}")
    elif args.command == "reset":
        paths = reset_all(args.seed_dir, args.runtime_dir)
        print(f"Reset {len(paths)} runtime files in {args.runtime_dir}")
    elif args.command == "serve":
        port = args.port or DEFAULT_PORTS[args.service]
        _serve(args.service, args.host, port, args.log_level)
    elif args.command == "dev":
        _dev(args.host)
    else:
        raise AssertionError(f"Unhandled command: {args.command}")


if __name__ == "__main__":
    main()
