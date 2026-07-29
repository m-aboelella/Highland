from __future__ import annotations

import json
import os
import socket
import subprocess
import sys
import time
from pathlib import Path

import httpx
import pytest

from highland.models.contracts import (
    ChatResponse,
    Citation,
    FinishReason,
    Message,
    MessageRole,
    RankedResult,
    RerankResponse,
    ResponseMetadata,
    ToolCall,
    Usage,
)
from highland.models.scripted import DeterministicEmbeddingModel, ScriptedChatModel
from highland.retrieval.contracts import chunk_document
from highland.retrieval.faiss_store import EmbeddingIndex
from highland.retrieval.hybrid import HybridRetriever, RetrievalFilters
from highland.retrieval.ingestion import normalize_record
from highland.runtime.agent import AgentLoop, AgentProfile, RunRepository, RunStatus
from highland.runtime.approvals import ApprovalService, ApprovalStore
from highland.runtime.events import EventType, RunEventStore
from highland.runtime.mcp import MCPGateway
from highland.runtime.policy import RunScope, ToolRegistry

ROOT = Path(__file__).parents[2]


class RelevantFirstReranker:
    name = "acceptance-reranker"

    async def rerank(self, request):
        terms = set(request.query.lower().split())
        ranked = sorted(
            enumerate(request.documents),
            key=lambda item: len(terms & set(item[1].text.lower().split())),
            reverse=True,
        )
        if request.top_n:
            ranked = ranked[: request.top_n]
        return RerankResponse(
            results=[
                RankedResult(index=index, relevance_score=1 - offset / 100, document=document)
                for offset, (index, document) in enumerate(ranked)
            ],
            metadata=ResponseMetadata(provider="scripted", model=self.name, simulated=True),
        )


def _response(content: str = "", *calls: ToolCall, citations=()) -> ChatResponse:
    return ChatResponse(
        message=Message(role=MessageRole.ASSISTANT, content=content, tool_calls=list(calls)),
        citations=list(citations),
        finish_reason=FinishReason.TOOL_CALL if calls else FinishReason.COMPLETE,
        usage=Usage(input_tokens=20, output_tokens=10),
        metadata=ResponseMetadata(provider="scripted", model="acceptance", simulated=True),
    )


def _port() -> int:
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        return listener.getsockname()[1]


def _start_service(service: str, port: int, tmp_path: Path) -> subprocess.Popen:
    environment = {
        **os.environ,
        "HIGHLAND_SEED_DIR": str(ROOT / "data" / "seed"),
        "HIGHLAND_RUNTIME_DIR": str(tmp_path / "mock-state"),
    }
    process = subprocess.Popen(
        [
            sys.executable,
            "-m",
            "highland_mocks.cli",
            "serve",
            service,
            "--port",
            str(port),
            "--log-level",
            "warning",
        ],
        env=environment,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline:
        try:
            if httpx.get(f"http://127.0.0.1:{port}/health", timeout=0.2).status_code == 200:
                return process
        except httpx.HTTPError:
            time.sleep(0.05)
    process.terminate()
    raise RuntimeError(f"{service} mock did not start")


async def _retrieval(tmp_path: Path):
    sources = {
        "archive": ("knowledge.json", "documents"),
        "relay": ("support.json", "tickets"),
        "beacon": ("observability.json", "incidents"),
    }
    chunks = []
    for source, (filename, collection) in sources.items():
        payload = json.loads((ROOT / "data" / "seed" / filename).read_text())
        for record in payload[collection]:
            chunks.extend(
                chunk
                for document in normalize_record(source, record)
                for chunk in chunk_document(document)
            )
    embeddings = DeterministicEmbeddingModel(dimensions=16)
    embedding_index = EmbeddingIndex(embeddings)
    vector_store = await embedding_index.build(chunks, tmp_path / "vectors")
    retriever = HybridRetriever(
        chunks,
        vector_store=vector_store,
        embedding_index=embedding_index,
        rerankers={"fast": RelevantFirstReranker()},
        candidate_limit=100,
        result_limit=30,
    )
    results = await retriever.search(
        "Northwind latency compaction runbook ticket incident memory",
        filters=RetrievalFilters(
            customer_id="cus_northwind",
            allowed_visibilities={"support", "company", "internal", "engineering"},
        ),
    )
    return results


@pytest.mark.asyncio
async def test_deployment_investigation_pauses_then_writes_once(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    support_port, observability_port = _port(), _port()
    processes = [
        _start_service("support", support_port, tmp_path),
        _start_service("observability", observability_port, tmp_path),
    ]
    monkeypatch.setenv("HIGHLAND_SUPPORT_URL", f"http://127.0.0.1:{support_port}")
    monkeypatch.setenv(
        "HIGHLAND_OBSERVABILITY_URL", f"http://127.0.0.1:{observability_port}"
    )
    try:
        retrieval = await _retrieval(tmp_path)
        categories = {result.chunk.source_system for result in retrieval.results}
        assert {"archive", "relay", "beacon"} <= categories
        event_store = RunEventStore(tmp_path / "events")
        event_store.append(
            "deployment",
            EventType.RETRIEVAL,
            {"source_categories": sorted(categories)},
        )
        commands = {
            name: (sys.executable, "-m", "highland_mocks.mcp_server", name)
            for name in ("support", "observability")
        }
        async with MCPGateway(commands) as gateway:
            registry = ToolRegistry.from_file(gateway, ROOT / "config" / "tool_policy.json")
            reads = (
                ToolCall(
                    id="metric-p95",
                    name="observability__query_deployment_metrics",
                    arguments={
                        "customer_id": "cus_northwind",
                        "metric": "retrieval.p95_ms",
                        "time_range": "24h",
                    },
                ),
                ToolCall(
                    id="metric-memory",
                    name="observability__query_deployment_metrics",
                    arguments={
                        "customer_id": "cus_northwind",
                        "metric": "retrieval.shard_3.memory_utilization",
                        "time_range": "24h",
                    },
                ),
                ToolCall(
                    id="ticket",
                    name="support__get_ticket",
                    arguments={"ticket_id": "tkt_1042"},
                ),
            )
            write = ToolCall(
                id="create-ticket",
                name="support__create_ticket",
                arguments={
                    "customer_id": "cus_northwind",
                    "title": "Controlled compaction investigation",
                    "description": (
                        "Track controlled compaction evidence; suspected memory contention "
                        "remains an unconfirmed hypothesis."
                    ),
                    "priority": "P2",
                    "category": "performance",
                },
            )
            final_text = (
                "The latency runbook recommends comparing volume and shard memory before "
                "scaling. Observed p95 and shard-3 memory rose while compaction was active; "
                "compaction remains a suspected, unconfirmed cause. Ticket created."
            )
            model = ScriptedChatModel(
                [
                    _response("", *reads),
                    _response("", write),
                    _response(
                        final_text,
                        citations=(
                            Citation(
                                start=4,
                                end=19,
                                text="latency runbook",
                                source_ids=[
                                    next(
                                        result.chunk.id
                                        for result in retrieval.results
                                        if result.chunk.source_system == "archive"
                                    )
                                ],
                            ),
                            Citation(
                                start=74,
                                end=87,
                                text="Observed p95",
                                tool_call_ids=["metric-p95", "metric-memory"],
                            ),
                        ),
                    ),
                ]
            )
            approvals = ApprovalStore(tmp_path / "approvals")
            runtime = AgentLoop(
                model,
                registry,
                AgentProfile.load(ROOT / "config" / "agents" / "general.json"),
                RunRepository(tmp_path / "states"),
                approvals,
                event_store,
            )
            outcome = await runtime.run(
                run_id="deployment",
                user_message="Investigate Northwind latency and prepare a ticket.",
                scope=RunScope(allowed_customers=frozenset({"cus_northwind"})),
            )
            assert outcome.status is RunStatus.PAUSED
            before = json.loads((tmp_path / "mock-state" / "support.json").read_text())
            before_count = len(before["tickets"])
            approval_id = outcome.pending_call["approval_id"]
            approvals.decide(approval_id, approve=True)
            service = ApprovalService(approvals, registry)
            completed = await runtime.resume_after_approval(
                run_id="deployment",
                approval_service=service,
            )
            approval_result = approvals.get(approval_id).result
            assert approval_result and not approval_result["is_error"], approval_result
            replayed = await runtime.resume_after_approval(
                run_id="deployment",
                approval_service=service,
            )
            after = json.loads((tmp_path / "mock-state" / "support.json").read_text())
            assert len(after["tickets"]) == before_count + 1
            assert completed == replayed
            assert "unconfirmed cause" in completed.content
            assert completed.citations[0].source_ids
            assert set(completed.citations[1].tool_call_ids) == {
                "metric-p95",
                "metric-memory",
            }
            trace = event_store.replay("deployment")
            assert any(event.type is EventType.APPROVAL_REQUIRED for event in trace)
            assert len(
                [
                    event
                    for event in trace
                    if event.type is EventType.TOOL_CALL
                    and event.payload["tool_call"]["name"] == "support__create_ticket"
                ]
            ) == 1
    finally:
        for process in processes:
            process.terminate()
        for process in processes:
            process.wait(timeout=5)
