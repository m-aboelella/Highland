from __future__ import annotations

import json
from pathlib import Path

from highland_mocks.constants import SERVICES
from highland_mocks.seed import DATASET_VERSION, generate_seed
from highland_mocks.systems import SYSTEMS


def _load(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def test_generator_is_deterministic(tmp_path: Path) -> None:
    first = tmp_path / "first"
    second = tmp_path / "second"
    generate_seed(first)
    generate_seed(second)
    for service in SERVICES:
        assert (first / f"{service}.json").read_bytes() == (second / f"{service}.json").read_bytes()


def test_each_source_owns_seed_rest_and_mcp_registration() -> None:
    assert tuple(SYSTEMS) == SERVICES
    for system in SYSTEMS.values():
        assert callable(system.seed_fragment)
        assert callable(system.register_routes)
        assert callable(system.register_tools)


def test_cross_system_customer_and_deployment_links(tmp_path: Path) -> None:
    generate_seed(tmp_path)
    crm = _load(tmp_path / "crm.json")
    support = _load(tmp_path / "support.json")
    observability = _load(tmp_path / "observability.json")
    communications = _load(tmp_path / "communications.json")
    projects = _load(tmp_path / "projects.json")

    customer_ids = {record["id"] for record in crm["customers"]}
    deployment_ids = {record["id"] for record in observability["deployments"]}

    assert all(
        deployment_id in deployment_ids
        for customer in crm["customers"]
        for deployment_id in customer["deployment_ids"]
    )
    for payload, collection in (
        (support, "tickets"),
        (communications, "messages"),
        (communications, "meetings"),
        (communications, "customer_updates"),
        (projects, "issues"),
    ):
        assert all(
            record.get("customer_id") in customer_ids
            for record in payload[collection]
            if record.get("customer_id")
        )


def test_every_source_record_has_provenance(tmp_path: Path) -> None:
    generate_seed(tmp_path)
    collections = {
        "crm": ["customers"],
        "knowledge": ["documents"],
        "support": ["tickets"],
        "observability": ["deployments", "incidents"],
        "communications": ["messages", "meetings", "customer_updates"],
        "projects": ["projects", "issues"],
    }
    for service, names in collections.items():
        payload = _load(tmp_path / f"{service}.json")
        assert payload["meta"]["dataset_version"] == DATASET_VERSION
        for name in names:
            for record in payload[name]:
                assert record["record_id"] == record["id"]
                assert record["source_system"]
                assert record["source_url"].startswith("https://")
