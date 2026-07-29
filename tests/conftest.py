from __future__ import annotations

import os
import socket
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from highland_mocks.api import create_app
from highland_mocks.seed import generate_seed
from highland_mocks.store import JsonStore


def pytest_collection_modifyitems(items: list[pytest.Item]) -> None:
    """Label tests by directory and keep live tests explicitly gated."""
    live_enabled = (
        os.getenv("HIGHLAND_RUN_LIVE_TESTS") == "1" and bool(os.getenv("COHERE_API_KEY"))
    )
    skip_live = pytest.mark.skip(
        reason="requires HIGHLAND_RUN_LIVE_TESTS=1 and COHERE_API_KEY"
    )
    for item in items:
        path = Path(str(item.path))
        if "live" in path.parts or item.get_closest_marker("live_model"):
            item.add_marker(pytest.mark.live_model)
            if not live_enabled:
                item.add_marker(skip_live)
        elif "acceptance" in path.parts:
            item.add_marker(pytest.mark.acceptance_scripted)
        elif "integration" in path.parts:
            item.add_marker(pytest.mark.integration_local)
        else:
            item.add_marker(pytest.mark.unit)


@pytest.fixture(autouse=True)
def _block_external_network(
    request: pytest.FixtureRequest, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Make an accidental network call in the hermetic suite fail visibly."""
    if request.node.get_closest_marker("live_model"):
        return

    original_create_connection = socket.create_connection
    original_connect = socket.socket.connect

    def guarded_create_connection(address: object, *args: object, **kwargs: object) -> object:
        if isinstance(address, tuple) and address[0] in {"127.0.0.1", "::1", "localhost"}:
            return original_create_connection(address, *args, **kwargs)
        raise RuntimeError(
            "external network is disabled in hermetic tests; use @pytest.mark.live_model "
            "with HIGHLAND_RUN_LIVE_TESTS=1"
        )

    def guarded_connect(instance: socket.socket, address: object) -> object:
        if isinstance(address, tuple) and address[0] in {"127.0.0.1", "::1", "localhost"}:
            return original_connect(instance, address)
        raise RuntimeError(
            "external network is disabled in hermetic tests; use @pytest.mark.live_model "
            "with HIGHLAND_RUN_LIVE_TESTS=1"
        )

    monkeypatch.setattr(socket, "create_connection", guarded_create_connection)
    monkeypatch.setattr(socket.socket, "connect", guarded_connect)


@pytest.fixture
def seed_dir(tmp_path: Path) -> Path:
    path = tmp_path / "seed"
    generate_seed(path)
    return path


@pytest.fixture
def runtime_dir(tmp_path: Path) -> Path:
    return tmp_path / "runtime"


def client_for(service: str, seed_dir: Path, runtime_dir: Path) -> TestClient:
    store = JsonStore(service, seed_dir=seed_dir, runtime_dir=runtime_dir)
    return TestClient(create_app(service, store=store))
