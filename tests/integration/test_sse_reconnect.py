from __future__ import annotations

import json
import threading
import time

from fastapi.testclient import TestClient

from highland.api import create_app
from highland.runtime.events import EventType, RunEventStore
from highland.settings import HighlandSettings


def test_sse_reconnect_resumes_after_last_event_without_duplicates(tmp_path) -> None:
    settings = HighlandSettings(workspace_dir=tmp_path)
    store = RunEventStore(tmp_path / "runs" / "events")
    store.append("run-1", EventType.RUN_STARTED, {})
    store.append("run-1", EventType.MODEL_DELTA, {"text": "A"})
    store.append("run-1", EventType.FINAL, {"content": "A"})
    client = TestClient(create_app(settings))
    response = client.get("/runs/run-1/events", headers={"Last-Event-ID": "1"})
    assert response.status_code == 200
    data = [
        json.loads(line.removeprefix("data: "))
        for line in response.text.splitlines()
        if line.startswith("data: ")
    ]
    assert [event["id"] for event in data] == [2, 3]
    assert len({event["id"] for event in data}) == 2


def test_sse_waits_for_events_appended_after_connection(tmp_path) -> None:
    settings = HighlandSettings(workspace_dir=tmp_path)
    store = RunEventStore(tmp_path / "runs" / "events")
    store.append("run-live", EventType.RUN_STARTED, {})

    def finish_run() -> None:
        time.sleep(0.05)
        store.append("run-live", EventType.MODEL_DELTA, {"text": "Later"})
        store.append("run-live", EventType.FINAL, {"content": "Later"})

    worker = threading.Thread(target=finish_run)
    worker.start()
    with TestClient(create_app(settings)) as client:
        response = client.get("/runs/run-live/events", headers={"Last-Event-ID": "1"})
    worker.join()

    data = [
        json.loads(line.removeprefix("data: "))
        for line in response.text.splitlines()
        if line.startswith("data: ")
    ]
    assert [event["type"] for event in data] == ["model_delta", "final"]
    assert response.headers["cache-control"] == "no-cache"
