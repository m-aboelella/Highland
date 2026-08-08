"""Discover conversations, asynchronous runs, evidence, and SSE routes."""

import asyncio
from uuid import uuid4

from fastapi import (
    APIRouter,
    BackgroundTasks,
    Header,
    HTTPException,
    Request,
    Response,
    status,
)
from fastapi.responses import StreamingResponse

from highland.discover.service import ChatRequest, SearchRequest
from highland.models.contracts import ModelError
from highland.retrieval.citations import CitationResolver
from highland.services import ApplicationServices

from .models import (
    CancelRunRequest,
    CreateConversationRequest,
    CreateMessageRequest,
    CreateRunRequest,
    RenameConversationRequest,
)


def create_discover_router(services: ApplicationServices) -> APIRouter:
    router = APIRouter(tags=["discover and runs"])
    conversations = services.conversations
    run_events = services.run_events

    @router.post("/conversations", status_code=status.HTTP_201_CREATED)
    async def create_conversation(
        request: CreateConversationRequest | None = None,
    ) -> dict[str, object]:
        return conversations.create(request.title if request else None).model_dump(mode="json")

    @router.get("/conversations")
    async def list_conversations() -> list[dict[str, object]]:
        return [item.model_dump(mode="json") for item in conversations.list()]

    @router.get("/conversations/{conversation_id}")
    async def get_conversation(conversation_id: str) -> dict[str, object]:
        try:
            return conversations.get(conversation_id).model_dump(mode="json")
        except FileNotFoundError:
            raise HTTPException(status_code=404, detail="Conversation not found") from None

    @router.patch("/conversations/{conversation_id}")
    async def rename_conversation(
        conversation_id: str,
        request: RenameConversationRequest,
    ) -> dict[str, object]:
        try:
            return conversations.rename(conversation_id, request.title).model_dump(mode="json")
        except FileNotFoundError:
            raise HTTPException(status_code=404, detail="Conversation not found") from None

    @router.delete("/conversations/{conversation_id}", status_code=status.HTTP_204_NO_CONTENT)
    async def delete_conversation(conversation_id: str) -> Response:
        try:
            conversations.delete(conversation_id)
        except FileNotFoundError:
            raise HTTPException(status_code=404, detail="Conversation not found") from None
        return Response(status_code=status.HTTP_204_NO_CONTENT)

    @router.post(
        "/conversations/{conversation_id}/messages", status_code=status.HTTP_201_CREATED
    )
    async def add_message(
        conversation_id: str,
        request: CreateMessageRequest,
    ) -> dict[str, object]:
        try:
            return conversations.append_message(
                conversation_id,
                role="user",
                content=request.content,
            ).model_dump(mode="json")
        except FileNotFoundError:
            raise HTTPException(status_code=404, detail="Conversation not found") from None

    @router.post(
        "/conversations/{conversation_id}/runs", status_code=status.HTTP_202_ACCEPTED
    )
    async def create_run(
        conversation_id: str,
        request: CreateRunRequest,
        background_tasks: BackgroundTasks,
    ) -> dict[str, object]:
        run_id = f"run_{uuid4().hex}"
        try:
            conversation = conversations.get(conversation_id)
            if conversation.messages:
                raise HTTPException(
                    status_code=status.HTTP_409_CONFLICT,
                    detail=(
                        "Discovery runs require a fresh conversation. "
                        "Create a new conversation before starting another run."
                    ),
                )
            message = conversations.append_message(
                conversation_id,
                role="user",
                content=request.content,
                run_id=run_id,
            )
        except FileNotFoundError:
            raise HTTPException(status_code=404, detail="Conversation not found") from None
        run_events.append(
            run_id,
            "run_started",
            {"conversation_id": conversation_id, "message_id": message.id},
        )
        background_tasks.add_task(
            _run_discovery,
            services,
            ChatRequest(
                conversation_id=conversation_id,
                query=request.content,
                filters=request.filters,
            ),
            run_id,
        )
        return {
            "run_id": run_id,
            "conversation_id": conversation_id,
            "message_id": message.id,
            "status": "queued",
            "events_url": f"/runs/{run_id}/events",
        }

    @router.post("/runs/{run_id}/cancel", status_code=status.HTTP_202_ACCEPTED)
    async def cancel_run(
        run_id: str,
        request: CancelRunRequest | None = None,
    ) -> dict[str, object]:
        payload = services.cancellations.cancel(
            run_id,
            reason=request.reason if request else None,
        )
        if run_events.summary(run_id)["status"] not in {"completed", "failed", "cancelled"}:
            run_events.append(run_id, "run_cancelled", payload)
        return {**payload, "status": "cancelled"}

    @router.post("/discover/search")
    async def discover_search(request: SearchRequest) -> dict[str, object]:
        try:
            result = await services.discover.search_sources(request)
        except FileNotFoundError as error:
            raise HTTPException(status_code=409, detail=str(error)) from error
        return result.model_dump(mode="json")

    @router.post("/discover/chat")
    async def discover_chat(request: ChatRequest) -> dict[str, object]:
        run_id = f"run_{uuid4().hex}"
        try:
            message = conversations.append_message(
                request.conversation_id,
                role="user",
                content=request.query,
                run_id=run_id,
            )
            outcome = await services.discover.chat(request, run_id=run_id)
            conversation = conversations.get(request.conversation_id)
        except FileNotFoundError as error:
            raise HTTPException(status_code=404, detail=str(error)) from error
        return {
            **outcome.model_dump(mode="json"),
            "conversation_id": request.conversation_id,
            "message_id": message.id,
            "sources": [
                source.model_dump(mode="json") for source in conversation.messages[-1].sources
            ],
        }

    @router.get("/runs/{run_id}/summary")
    async def run_summary(run_id: str) -> dict[str, object]:
        return run_events.summary(run_id)

    @router.get("/runs")
    async def list_runs() -> list[dict[str, object]]:
        return run_events.list_summaries()

    @router.get("/runs/{run_id}/trace")
    async def run_trace(run_id: str) -> list[dict[str, object]]:
        return [event.model_dump(mode="json") for event in run_events.replay(run_id)]

    @router.get("/runs/{run_id}/evidence")
    async def run_evidence(run_id: str) -> dict[str, object]:
        events = run_events.replay(run_id)
        retrieval = next(
            (event.payload for event in events if event.type.value == "retrieval"),
            {},
        )
        citations = [event.payload for event in events if event.type.value == "citation"]
        source_ids = {
            source_id
            for citation in citations
            for source_id in citation.get("source_ids", [])
            if isinstance(source_id, str)
        }
        resolver = CitationResolver(services.workspace.indexes / "search")
        evidence = []
        for chunk_id in sorted(source_ids):
            chunk = resolver.get_chunk(chunk_id)
            if chunk is not None:
                evidence.append(chunk.model_dump(mode="json"))
        return {
            "run_id": run_id,
            "filters": retrieval.get("filters", {}),
            "citations": citations,
            "evidence": evidence,
            "diagnostics": retrieval.get("diagnostics", []),
            "timings": retrieval.get("timings", {}),
            "refreshed_through_mcp": any(
                event.type.value == "tool_result" for event in events
            ),
        }

    @router.get("/runs/{run_id}/events")
    async def stream_run_events(
        run_id: str,
        request: Request,
        last_event_id: int = Header(default=0, alias="Last-Event-ID"),
    ) -> StreamingResponse:
        if run_events.summary(run_id)["status"] == "missing":
            raise HTTPException(status_code=404, detail="Run not found")

        async def stream():
            cursor = last_event_id
            heartbeat_at = asyncio.get_running_loop().time()
            terminal_types = {"error", "final", "run_cancelled", "run_completed", "run_failed"}
            while True:
                if await request.is_disconnected():
                    return
                events = run_events.replay(run_id, after_id=cursor)
                for event in events:
                    cursor = event.id
                    yield run_events.sse(event)
                    if event.type.value in terminal_types:
                        return
                summary = run_events.summary(run_id)
                if (
                    summary["status"] in {"completed", "failed", "cancelled"}
                    and cursor >= int(summary["last_event_id"])
                ):
                    return
                now = asyncio.get_running_loop().time()
                if now - heartbeat_at >= 15:
                    yield ": keep-alive\n\n"
                    heartbeat_at = now
                await asyncio.sleep(0.1)

        return StreamingResponse(
            stream(),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
        )

    return router


async def _run_discovery(
    services: ApplicationServices,
    request: ChatRequest,
    run_id: str,
) -> None:
    try:
        await services.discover.chat(request, run_id=run_id)
    except Exception as error:  # noqa: BLE001 - persist background failure for the UI
        payload: dict[str, object] = {
            "error_type": type(error).__name__,
            "message": str(error),
        }
        if isinstance(error, ModelError):
            payload.update(
                {
                    "code": error.code,
                    "provider": error.provider,
                    "request_id": error.request_id,
                    "retryable": error.retryable,
                }
            )
        services.run_events.append(run_id, "error", payload)
