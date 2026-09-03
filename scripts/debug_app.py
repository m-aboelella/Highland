from __future__ import annotations

import os
import sys

import debugpy  # noqa: T100 - this module is the explicit debugger entry point


def main() -> None:
    host = os.environ.get("HIGHLAND_DEBUG_HOST", "127.0.0.1")
    port = int(os.environ.get("HIGHLAND_DEBUG_PORT", "5678"))
    debugpy.configure(subProcess=True)
    debugpy.listen((host, port))  # noqa: T100

    try:
        debugpy.wait_for_client()  # noqa: T100
        from highland.cli import main as highland_main

        sys.argv = ["highland", "app"]
        highland_main()
    except KeyboardInterrupt:
        return


if __name__ == "__main__":
    main()
