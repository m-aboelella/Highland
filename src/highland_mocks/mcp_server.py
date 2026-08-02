from __future__ import annotations

import argparse
import os

from mcp.server.fastmcp import FastMCP

from .constants import DEFAULT_PORTS, SERVICE_PRODUCTS
from .systems import SYSTEMS
from .systems.common import SourceClient


def _base_url(connector: str) -> str:
    variable = f"HIGHLAND_{connector.upper()}_URL"
    return os.getenv(variable, f"http://localhost:{DEFAULT_PORTS[connector]}").rstrip("/")


def build_mcp(connector: str) -> FastMCP:
    if connector not in SYSTEMS:
        raise ValueError(f"Unknown connector: {connector}")
    product = SERVICE_PRODUCTS[connector]
    mcp = FastMCP(
        name=f"Highland — {product}",
        instructions=(
            f"Atomic tools for the synthetic {product} service. All records are fictional. "
            "Preserve record IDs and source URLs in downstream evidence."
        ),
    )
    SYSTEMS[connector].register_tools(mcp, SourceClient(connector, _base_url(connector)))
    return mcp


def main() -> None:
    parser = argparse.ArgumentParser(
        prog="highland-mcp",
        description="Run one Highland mock-system MCP connector over stdio.",
    )
    parser.add_argument("connector", choices=SYSTEMS)
    args = parser.parse_args()
    build_mcp(args.connector).run(transport="stdio")


if __name__ == "__main__":
    main()
