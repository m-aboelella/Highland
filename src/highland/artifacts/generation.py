from __future__ import annotations

import re
from collections.abc import Sequence

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from highland.models.contracts import (
    ChatModel,
    ChatRequest,
    Document,
    Message,
    MessageRole,
    ModelCapabilities,
)

from .repository import Artifact, ArtifactCitation, ArtifactRepository, ArtifactType


class ArtifactGenerationError(ValueError):
    """The model returned an artifact that failed deterministic validation."""


class GenerationModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class PlannedSection(GenerationModel):
    heading: str = Field(min_length=1)
    purpose: str = Field(min_length=1)


class SectionPlan(GenerationModel):
    title: str = Field(min_length=1)
    sections: list[PlannedSection] = Field(min_length=1)


class GeneratedSection(GenerationModel):
    heading: str = Field(min_length=1)
    markdown: str
    citation_ids: list[str] = Field(default_factory=list)


class GeneratedArtifact(GenerationModel):
    title: str = Field(min_length=1)
    sections: list[GeneratedSection] = Field(min_length=1)


class RevisedSection(GenerationModel):
    heading: str = Field(min_length=1)
    markdown: str
    citation_ids: list[str] = Field(default_factory=list)
    invalidated_citation_ids: list[str] = Field(default_factory=list)


class SectionRevisionPreview(GenerationModel):
    artifact_id: str
    expected_revision: int
    heading: str
    original_markdown: str
    proposed_markdown: str
    resulting_content: str
    citations: list[ArtifactCitation]
    invalidated_citation_ids: list[str] = Field(default_factory=list)


PLAN_SCHEMA = SectionPlan.model_json_schema()
ARTIFACT_SCHEMA = GeneratedArtifact.model_json_schema()
REVISION_SCHEMA = RevisedSection.model_json_schema()
_heading = re.compile(r"^(#{1,6})[ \t]+(.+?)[ \t]*$", flags=re.MULTILINE)


class ArtifactGenerator:
    def __init__(self, chat_model: ChatModel, repository: ArtifactRepository) -> None:
        self.chat_model = chat_model
        self.repository = repository

    async def generate(
        self,
        *,
        artifact_type: ArtifactType,
        answer: str,
        evidence: Sequence[ArtifactCitation],
        conversation_id: str,
        run_id: str,
        message_id: str | None = None,
        instructions: str | None = None,
    ) -> Artifact:
        documents = _evidence_documents(evidence)
        plan_response = await self.chat_model.chat(
            ChatRequest(
                messages=[
                    Message(
                        role=MessageRole.SYSTEM,
                        content=(
                            "Plan a concise evidence-backed artifact. Return only the requested "
                            "structured output. Do not invent facts or source identifiers."
                        ),
                    ),
                    Message(
                        role=MessageRole.USER,
                        content=(
                            f"Artifact type: {artifact_type.value}\n"
                            f"Instructions: {instructions or 'Use the selected answer faithfully.'}\n"
                            f"Selected answer:\n{answer}"
                        ),
                    ),
                ],
                documents=documents,
                response_schema=PLAN_SCHEMA,
                required_capabilities=ModelCapabilities(structured_output=True),
                logical_call_id="artifact-section-plan",
            )
        )
        plan = _validated_output(SectionPlan, plan_response.structured_output)
        draft_response = await self.chat_model.chat(
            ChatRequest(
                messages=[
                    Message(
                        role=MessageRole.SYSTEM,
                        content=(
                            "Draft the artifact from the selected answer and supplied evidence. "
                            "Markdown tables are allowed for structured facts. Put citation labels "
                            "such as [E1] directly after supported claims and return each used label "
                            "in citation_ids. Use only supplied evidence IDs."
                        ),
                    ),
                    Message(
                        role=MessageRole.USER,
                        content=(
                            f"Artifact type: {artifact_type.value}\n"
                            f"Approved section plan:\n{plan.model_dump_json(indent=2)}\n"
                            f"Selected answer:\n{answer}"
                        ),
                    ),
                ],
                documents=documents,
                response_schema=ARTIFACT_SCHEMA,
                required_capabilities=ModelCapabilities(structured_output=True),
                logical_call_id="artifact-cited-draft",
            )
        )
        generated = _validated_output(GeneratedArtifact, draft_response.structured_output)
        citations = _resolve_citations(
            (citation_id for section in generated.sections for citation_id in section.citation_ids),
            evidence,
        )
        return self.repository.create(
            title=generated.title,
            artifact_type=artifact_type,
            content=_render_sections(generated.sections),
            citations=citations,
            conversation_id=conversation_id,
            run_id=run_id,
            message_id=message_id,
        )

    async def preview_section_revision(
        self,
        artifact_id: str,
        *,
        expected_revision: int,
        heading: str,
        instructions: str,
    ) -> SectionRevisionPreview:
        artifact = self.repository.get(artifact_id)
        if artifact.revision != expected_revision:
            raise ArtifactGenerationError(
                f"Artifact is at revision {artifact.revision}, not {expected_revision}"
            )
        start, end, original = _section_span(artifact.content, heading)
        response = await self.chat_model.chat(
            ChatRequest(
                messages=[
                    Message(
                        role=MessageRole.SYSTEM,
                        content=(
                            "Revise only the selected Markdown section. Preserve the heading and "
                            "return all retained evidence IDs. Explicitly list citations invalidated "
                            "by the edit. Use no evidence ID outside the supplied documents."
                        ),
                    ),
                    Message(
                        role=MessageRole.USER,
                        content=(
                            f"Selected heading: {heading}\nInstructions: {instructions}\n"
                            f"Selected section:\n{original}"
                        ),
                    ),
                ],
                documents=_evidence_documents(artifact.citations),
                response_schema=REVISION_SCHEMA,
                required_capabilities=ModelCapabilities(structured_output=True),
                logical_call_id="artifact-section-revision",
            )
        )
        revised = _validated_output(RevisedSection, response.structured_output)
        if revised.heading.casefold() != heading.casefold():
            raise ArtifactGenerationError("Model changed the selected section heading")
        retained = _resolve_citations(revised.citation_ids, artifact.citations)
        invalidated = set(revised.invalidated_citation_ids)
        known = {citation.id for citation in artifact.citations}
        if not invalidated.issubset(known):
            raise ArtifactGenerationError("Model invalidated an unknown citation ID")
        retained_ids = {citation.id for citation in retained}
        citations = [
            citation
            for citation in artifact.citations
            if citation.id not in invalidated or citation.id in retained_ids
        ]
        proposed = f"## {revised.heading}\n\n{revised.markdown.strip()}"
        return SectionRevisionPreview(
            artifact_id=artifact.id,
            expected_revision=artifact.revision,
            heading=heading,
            original_markdown=original,
            proposed_markdown=proposed,
            resulting_content=artifact.content[:start] + proposed + artifact.content[end:],
            citations=citations,
            invalidated_citation_ids=sorted(invalidated),
        )


def _validated_output(model: type[GenerationModel], value: object) -> GenerationModel:
    try:
        return model.model_validate(value)
    except ValidationError as error:
        raise ArtifactGenerationError(f"Invalid structured model output: {error}") from error


def _evidence_documents(evidence: Sequence[ArtifactCitation]) -> list[Document]:
    return [
        Document(
            id=item.id,
            text=item.passage or item.title or item.source_id,
            metadata={
                "source_id": item.source_id,
                "source_url": item.source_url,
                "updated_at": item.updated_at.isoformat() if item.updated_at else "",
            },
        )
        for item in evidence
    ]


def _resolve_citations(
    citation_ids: Sequence[str] | object,
    evidence: Sequence[ArtifactCitation],
) -> list[ArtifactCitation]:
    by_id = {item.id: item for item in evidence}
    ordered_ids = list(dict.fromkeys(citation_ids))
    unknown = [citation_id for citation_id in ordered_ids if citation_id not in by_id]
    if unknown:
        raise ArtifactGenerationError(f"Unknown evidence IDs: {', '.join(unknown)}")
    return [by_id[citation_id] for citation_id in ordered_ids]


def _render_sections(sections: Sequence[GeneratedSection]) -> str:
    return "\n\n".join(
        f"## {section.heading.strip()}\n\n{section.markdown.strip()}" for section in sections
    ).rstrip() + "\n"


def _section_span(content: str, heading: str) -> tuple[int, int, str]:
    matches = list(_heading.finditer(content))
    selected = next(
        (index for index, match in enumerate(matches) if match.group(2).casefold() == heading.casefold()),
        None,
    )
    if selected is None:
        raise ArtifactGenerationError(f"Section not found: {heading}")
    start = matches[selected].start()
    end = matches[selected + 1].start() if selected + 1 < len(matches) else len(content)
    return start, end, content[start:end].rstrip()
