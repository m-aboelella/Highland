from __future__ import annotations

import json
import re
from collections.abc import Sequence
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from highland.models.contracts import (
    ChatModel,
    ChatRequest,
    Message,
    MessageRole,
    ModelCapabilities,
    ToolDefinition,
    ToolResult,
)

from .repository import Artifact, ArtifactCitation, ArtifactRepository


class ArtifactAssistantError(ValueError):
    """The artifact-scoped assistant could not produce a safe edit proposal."""


class AssistantModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ArtifactAssistantMessage(AssistantModel):
    role: Literal["user", "assistant"]
    content: str = Field(min_length=1, max_length=20_000)


class ProposedArtifactEdit(AssistantModel):
    summary: str = Field(min_length=1, max_length=1_000)
    content: str = Field(min_length=1, max_length=1_000_000)
    citation_ids: list[str] = Field(default_factory=list)


class ArtifactAssistantOperation(AssistantModel):
    tool: Literal[
        "read_artifact",
        "read_saved_evidence",
        "propose_markdown_edit",
        "write_artifact_revision",
    ]
    label: str
    detail: str
    status: Literal["completed", "awaiting_approval"]


class ArtifactAssistantPreview(AssistantModel):
    artifact_id: str
    expected_revision: int
    assistant_message: str
    summary: str
    proposed_content: str
    citations: list[ArtifactCitation]
    operations: list[ArtifactAssistantOperation]
    model: str


_ARTIFACT_TOOLS = [
    ToolDefinition(
        name="read_artifact",
        description=(
            "Read the current saved Markdown artifact before editing it. This tool is scoped to "
            "the open artifact and cannot read or alter the originating discovery conversation."
        ),
        input_schema={"type": "object", "properties": {}, "additionalProperties": False},
    ),
    ToolDefinition(
        name="read_saved_evidence",
        description=(
            "Read evidence records already attached to the artifact. Use this before adding or "
            "changing factual claims. An evidence ID such as E1 means Evidence 1."
        ),
        input_schema={
            "type": "object",
            "properties": {
                "evidence_ids": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Evidence IDs to read. An empty list reads all saved evidence.",
                }
            },
            "required": ["evidence_ids"],
            "additionalProperties": False,
        },
    ),
    ToolDefinition(
        name="propose_markdown_edit",
        description=(
            "Stage a complete replacement Markdown draft for human review. This does not write "
            "the file. Preserve relevant evidence markers such as [E1]."
        ),
        input_schema={
            "type": "object",
            "properties": {
                "summary": {
                    "type": "string",
                    "description": "A concise explanation of the proposed changes.",
                },
                "content": {
                    "type": "string",
                    "description": "The complete proposed Markdown artifact.",
                },
                "citation_ids": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Every saved evidence ID referenced in the proposed Markdown.",
                },
            },
            "required": ["summary", "content", "citation_ids"],
            "additionalProperties": False,
        },
    ),
]
_EVIDENCE_MARKER = re.compile(r"\[(E\d+)\]")


class ArtifactAssistant:
    """A bounded Cohere tool loop that can read one artifact and stage an edit."""

    def __init__(self, chat_model: ChatModel, repository: ArtifactRepository) -> None:
        self.chat_model = chat_model
        self.repository = repository

    async def preview_edit(
        self,
        artifact_id: str,
        *,
        expected_revision: int,
        instruction: str,
        history: Sequence[ArtifactAssistantMessage] = (),
        draft_content: str | None = None,
    ) -> ArtifactAssistantPreview:
        artifact = self.repository.get(artifact_id)
        if artifact.revision != expected_revision:
            raise ArtifactAssistantError(
                f"Artifact is at revision {artifact.revision}, not {expected_revision}"
            )
        working_artifact = (
            artifact.model_copy(update={"content": draft_content})
            if draft_content is not None
            else artifact
        )
        messages = [
            Message(
                role=MessageRole.SYSTEM,
                content=(
                    "You are an artifact-scoped file editing assistant. Work only on the open "
                    "Markdown artifact. Never continue or modify its originating discovery run. "
                    "The open content may be an unsaved working draft from earlier turns; refine "
                    "that draft when present. First call read_artifact. Call read_saved_evidence before introducing or "
                    "changing factual claims. Then call propose_markdown_edit with the complete "
                    "replacement Markdown. Preserve useful structure and valid evidence markers. "
                    "Do not claim that a proposal has been written; a human must approve it."
                ),
            )
        ]
        messages.extend(
            Message(
                role=MessageRole.USER if turn.role == "user" else MessageRole.ASSISTANT,
                content=turn.content,
            )
            for turn in history[-12:]
        )
        messages.append(Message(role=MessageRole.USER, content=instruction))
        operations: list[ArtifactAssistantOperation] = []
        read_artifact = False
        last_model = str(getattr(self.chat_model, "name", "configured chat model"))

        for step in range(1, 7):
            response = await self.chat_model.chat(
                ChatRequest(
                    messages=messages,
                    tools=_ARTIFACT_TOOLS,
                    required_capabilities=ModelCapabilities(tools=True),
                    logical_call_id=f"artifact-assistant:{artifact.id}:{step}",
                )
            )
            last_model = response.metadata.model
            messages.append(response.message)
            if not response.message.tool_calls:
                raise ArtifactAssistantError(
                    "The assistant finished without staging a Markdown edit. Try a more specific instruction."
                )

            results: list[ToolResult] = []
            for call in response.message.tool_calls:
                try:
                    result, operation, proposal = self._execute_tool(
                        working_artifact,
                        call.name,
                        dict(call.arguments),
                        read_artifact=read_artifact,
                        working_draft=draft_content is not None,
                    )
                except ArtifactAssistantError as error:
                    results.append(
                        ToolResult(tool_call_id=call.id, content=str(error), is_error=True)
                    )
                    continue
                results.append(ToolResult(tool_call_id=call.id, content=result))
                if operation and not any(
                    item.tool == operation.tool and item.detail == operation.detail
                    for item in operations
                ):
                    operations.append(operation)
                if call.name == "read_artifact":
                    read_artifact = True
                if proposal is not None:
                    operations.append(
                        ArtifactAssistantOperation(
                            tool="write_artifact_revision",
                            label="Write a new revision",
                            detail=(
                                f"Revision {artifact.revision + 1} will be written only after you approve."
                            ),
                            status="awaiting_approval",
                        )
                    )
                    return ArtifactAssistantPreview(
                        artifact_id=artifact.id,
                        expected_revision=artifact.revision,
                        assistant_message=proposal.summary,
                        summary=proposal.summary,
                        proposed_content=proposal.content,
                        citations=artifact.citations,
                        operations=operations,
                        model=last_model,
                    )
            messages.append(Message(role=MessageRole.TOOL, tool_results=results))
        raise ArtifactAssistantError("The assistant exceeded its six-step artifact editing limit")

    def _execute_tool(
        self,
        artifact: Artifact,
        name: str,
        arguments: dict[str, object],
        *,
        read_artifact: bool,
        working_draft: bool,
    ) -> tuple[str, ArtifactAssistantOperation | None, ProposedArtifactEdit | None]:
        if name == "read_artifact":
            return (
                json.dumps(
                    {
                        "artifact_id": artifact.id,
                        "title": artifact.title,
                        "revision": artifact.revision,
                        "markdown": artifact.content,
                    }
                ),
                ArtifactAssistantOperation(
                    tool="read_artifact",
                    label=("Read the working draft" if working_draft else "Read the Markdown file"),
                    detail=(
                        f"Opened the conversational draft based on saved revision {artifact.revision}."
                        if working_draft
                        else f"Opened {artifact.title}, revision {artifact.revision}."
                    ),
                    status="completed",
                ),
                None,
            )
        if name == "read_saved_evidence":
            requested = arguments.get("evidence_ids", [])
            if not isinstance(requested, list) or not all(isinstance(item, str) for item in requested):
                raise ArtifactAssistantError("evidence_ids must be a list of saved evidence IDs")
            known = {item.id: item for item in artifact.citations}
            selected_ids = requested or list(known)
            unknown = [item for item in selected_ids if item not in known]
            if unknown:
                raise ArtifactAssistantError(f"Unknown saved evidence IDs: {', '.join(unknown)}")
            selected = [known[item] for item in selected_ids]
            return (
                json.dumps(
                    [
                        {
                            "id": item.id,
                            "meaning": f"Evidence {item.label}",
                            "title": item.title,
                            "source_system": item.source_system,
                            "source_id": item.source_id,
                            "passage": item.passage,
                            "updated_at": item.updated_at.isoformat() if item.updated_at else None,
                        }
                        for item in selected
                    ]
                ),
                ArtifactAssistantOperation(
                    tool="read_saved_evidence",
                    label="Read saved evidence",
                    detail=(
                        f"Read {len(selected)} attached evidence record"
                        f"{'s' if len(selected) != 1 else ''}: "
                        f"{', '.join(item.id for item in selected) or 'none'}."
                    ),
                    status="completed",
                ),
                None,
            )
        if name == "propose_markdown_edit":
            if not read_artifact:
                raise ArtifactAssistantError("Call read_artifact before proposing an edit")
            try:
                proposal = ProposedArtifactEdit.model_validate(arguments)
            except ValidationError as error:
                raise ArtifactAssistantError(f"Invalid Markdown edit proposal: {error}") from error
            known = {item.id for item in artifact.citations}
            referenced = set(_EVIDENCE_MARKER.findall(proposal.content))
            declared = set(proposal.citation_ids)
            unknown = sorted((referenced | declared) - known)
            if unknown:
                raise ArtifactAssistantError(
                    f"Proposed Markdown uses unknown evidence IDs: {', '.join(unknown)}"
                )
            if referenced != declared:
                raise ArtifactAssistantError(
                    "citation_ids must exactly match the evidence markers used in the proposed Markdown"
                )
            if proposal.content == artifact.content:
                raise ArtifactAssistantError("The proposed Markdown does not change the artifact")
            return (
                "Markdown proposal staged for human approval; the artifact has not been written.",
                ArtifactAssistantOperation(
                    tool="propose_markdown_edit",
                    label="Propose a Markdown change",
                    detail=proposal.summary,
                    status="completed",
                ),
                proposal,
            )
        raise ArtifactAssistantError(f"Tool {name!r} is not available in the artifact workspace")
