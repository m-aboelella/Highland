from __future__ import annotations

from datetime import UTC, datetime, timedelta
from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

from highland.models.contracts import (
    ChatModel,
    ChatRequest,
    Document,
    Message,
    MessageRole,
    ModelCapabilities,
)

from .repository import Artifact, ArtifactCitation


class EvidenceCoverageError(ValueError):
    """Claim extraction contained invalid spans or evidence identifiers."""


class CoverageModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ProposedClaim(CoverageModel):
    text: str = Field(min_length=1)
    start: int = Field(ge=0)
    end: int = Field(gt=0)
    citation_ids: list[str] = Field(default_factory=list)
    model_assessment: str = Field(pattern="^(supported|weak|unsupported)$")

    @model_validator(mode="after")
    def end_follows_start(self) -> ProposedClaim:
        if self.end <= self.start:
            raise ValueError("claim end must follow start")
        return self


class ProposedCoverage(CoverageModel):
    claims: list[ProposedClaim] = Field(default_factory=list)


class ClaimSupport(StrEnum):
    SUPPORTED = "supported"
    WEAKLY_SUPPORTED = "weakly_supported"
    UNSUPPORTED = "unsupported"
    STALE = "stale"


class CheckedClaim(CoverageModel):
    text: str
    start: int
    end: int
    citation_ids: list[str]
    status: ClaimSupport
    explanation: str


class EvidenceCoverageReport(CoverageModel):
    artifact_id: str
    artifact_revision: int
    checked_at: datetime
    claims: list[CheckedClaim]
    advisory: str = (
        "Coverage labels are model-assisted review signals, not mathematical guarantees."
    )


COVERAGE_SCHEMA = ProposedCoverage.model_json_schema()


class EvidenceCoverageChecker:
    def __init__(self, chat_model: ChatModel, *, stale_after_days: int = 90) -> None:
        self.chat_model = chat_model
        self.stale_after = timedelta(days=stale_after_days)

    async def check(
        self,
        artifact: Artifact,
        *,
        checked_at: datetime | None = None,
    ) -> EvidenceCoverageReport:
        now = checked_at or datetime.now(UTC)
        response = await self.chat_model.chat(
            ChatRequest(
                messages=[
                    Message(
                        role=MessageRole.SYSTEM,
                        content=(
                            "Extract factual claims from the Markdown and propose supporting "
                            "evidence IDs. Return exact character offsets into the complete Markdown. "
                            "Classify support conservatively. Use only supplied evidence IDs. This "
                            "assessment will be deterministically validated."
                        ),
                    ),
                    Message(role=MessageRole.USER, content=artifact.content),
                ],
                documents=[
                    Document(
                        id=citation.id,
                        text=citation.passage or citation.title or citation.source_id,
                        metadata={
                            "source_id": citation.source_id,
                            "updated_at": (
                                citation.updated_at.isoformat() if citation.updated_at else ""
                            ),
                        },
                    )
                    for citation in artifact.citations
                ],
                response_schema=COVERAGE_SCHEMA,
                required_capabilities=ModelCapabilities(structured_output=True),
                logical_call_id="artifact-evidence-coverage",
            )
        )
        try:
            proposed = ProposedCoverage.model_validate(response.structured_output)
        except ValidationError as error:
            raise EvidenceCoverageError(f"Invalid structured claim extraction: {error}") from error
        citations = {citation.id: citation for citation in artifact.citations}
        checked: list[CheckedClaim] = []
        for claim in proposed.claims:
            if claim.end > len(artifact.content) or artifact.content[claim.start : claim.end] != claim.text:
                raise EvidenceCoverageError(f"Invalid span for claim: {claim.text}")
            unknown = [citation_id for citation_id in claim.citation_ids if citation_id not in citations]
            if unknown:
                raise EvidenceCoverageError(f"Unknown evidence IDs: {', '.join(unknown)}")
            status, explanation = _support_status(claim, citations, now, self.stale_after)
            checked.append(
                CheckedClaim(
                    **claim.model_dump(exclude={"model_assessment"}),
                    status=status,
                    explanation=explanation,
                )
            )
        return EvidenceCoverageReport(
            artifact_id=artifact.id,
            artifact_revision=artifact.revision,
            checked_at=now,
            claims=checked,
        )


def _support_status(
    claim: ProposedClaim,
    citations: dict[str, ArtifactCitation],
    now: datetime,
    stale_after: timedelta,
) -> tuple[ClaimSupport, str]:
    if not claim.citation_ids or claim.model_assessment == "unsupported":
        return ClaimSupport.UNSUPPORTED, "No valid supporting evidence was mapped."
    evidence = [citations[citation_id] for citation_id in claim.citation_ids]
    if any(
        item.updated_at is not None and now - item.updated_at > stale_after
        for item in evidence
    ):
        return ClaimSupport.STALE, "At least one mapped source is older than the freshness window."
    if claim.model_assessment == "weak":
        return ClaimSupport.WEAKLY_SUPPORTED, "The model found only partial or indirect support."
    return ClaimSupport.SUPPORTED, "Mapped citation IDs and the claim span are valid."
