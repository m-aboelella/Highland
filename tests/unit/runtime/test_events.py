from __future__ import annotations

from highland.runtime.events import EventType, RunEventStore


def test_append_replay_summary_and_redaction(tmp_path) -> None:
    store = RunEventStore(tmp_path)
    first = store.append("run", EventType.RUN_STARTED, {"api_key": "secret"})
    store.append(
        "run",
        EventType.TOOL_RESULT,
        {"result": {"authorization": "Bearer x", "value": 3}},
    )
    final = store.append("run", EventType.FINAL, {"content": "done"})
    events = store.replay("run")
    assert [event.id for event in events] == [1, 2, 3]
    assert first.payload["api_key"] == "[REDACTED]"
    assert events[1].payload["result"]["authorization"] == "[REDACTED]"
    assert store.summary("run") == {
        "run_id": "run",
        "event_count": 3,
        "status": "completed",
        "final": {"content": "done"},
        "last_event_id": final.id,
    }


def test_truncated_final_line_is_ignored(tmp_path) -> None:
    store = RunEventStore(tmp_path)
    store.append("run", EventType.RUN_STARTED, {})
    with (tmp_path / "run.events.jsonl").open("a") as stream:
        stream.write('{"version":1,"id":2')
    assert [event.id for event in store.replay("run")] == [1]
    assert store.append("run", EventType.ERROR, {}).id == 2


def test_replay_after_event_id_has_no_duplicates(tmp_path) -> None:
    store = RunEventStore(tmp_path)
    for index in range(3):
        store.append("run", EventType.MODEL_DELTA, {"index": index})
    assert [event.id for event in store.replay("run", after_id=2)] == [3]
