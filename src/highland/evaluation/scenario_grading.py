from __future__ import annotations

import re
from typing import Any

from highland.runtime.agent import RunStatus
from highland.runtime.events import EventType, RunEvent

_CUSTOMER_ID = re.compile(r"cus_[a-z0-9_-]+", re.IGNORECASE)


def customer_ids(value: object) -> set[str]:
    return set(_CUSTOMER_ID.findall(str(value)))


def _tool_name(event: RunEvent) -> str | None:
    call = event.payload.get("tool_call")
    return str(call.get("name")) if isinstance(call, dict) and call.get("name") else None


def check_discover_trace(
    manifest: dict[str, Any],
    events: list[RunEvent],
    status: RunStatus,
) -> list[str]:
    """Grade facts that are authoritative in persisted production trace events."""
    failures: list[str] = []
    types = {event.type for event in events}
    if EventType.RETRIEVAL not in types or EventType.MODEL_CALL not in types:
        failures.append("production trace is missing retrieval or model events")
    terminal = EventType.FINAL if status is RunStatus.COMPLETED else EventType.APPROVAL_REQUIRED
    if terminal not in types:
        failures.append(f"production trace is missing {terminal.value}")
    called = {
        name.partition("__")[2]
        for event in events
        if event.type is EventType.TOOL_CALL and (name := _tool_name(event))
    }
    required = {str(name) for name in manifest.get("required_tools", [])}
    missing_tools = sorted(required - called)
    if missing_tools:
        failures.append(f"required tools not called: {', '.join(missing_tools)}")
    retrieval = next((event for event in events if event.type is EventType.RETRIEVAL), None)
    indexed = retrieval.payload.get("results", []) if retrieval else []
    by_chunk = {
        str(item["chunk_id"]): item
        for item in indexed
        if isinstance(item, dict) and item.get("chunk_id")
    }
    cited_chunks = {
        str(source_id)
        for event in events
        if event.type is EventType.CITATION
        for source_id in event.payload.get("source_ids", [])
    }
    invalid = sorted(cited_chunks - set(by_chunk))
    if invalid:
        failures.append(f"invalid citation IDs: {', '.join(invalid)}")
    cited_sources = {
        str(by_chunk[chunk_id]["source_id"])
        for chunk_id in cited_chunks
        if chunk_id in by_chunk
    }
    required_evidence = {
        str(source_id)
        for claim in manifest.get("expected_claims", []) or []
        for source_id in claim.get("evidence", [])
        if any(str(item.get("source_id")) == str(source_id) for item in indexed)
    }
    missing_evidence = sorted(required_evidence - cited_sources)
    if missing_evidence:
        failures.append(f"retrieved required sources not cited: {', '.join(missing_evidence)}")
    allowed = set(manifest.get("allowed_customers") or [])
    if allowed:
        leaked = sorted(
            {
                str(item.get("chunk", {}).get("customer_id"))
                for item in indexed
                if item.get("chunk", {}).get("customer_id")
                and item.get("chunk", {}).get("customer_id") not in allowed
            }
        )
        tool_customer_ids = {
            customer
            for event in events
            if event.type is EventType.TOOL_RESULT
            for customer in customer_ids(event.payload)
            if customer not in allowed
        }
        leaked.extend(sorted(tool_customer_ids))
        if leaked:
            failures.append(f"customer leakage: {', '.join(dict.fromkeys(leaked))}")
    return failures
