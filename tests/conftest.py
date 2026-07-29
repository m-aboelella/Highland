from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from highland_mocks.api import create_app
from highland_mocks.seed import generate_seed
from highland_mocks.store import JsonStore


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
