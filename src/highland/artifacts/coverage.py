from __future__ import annotations

import re
from datetime import UTC, datetime, timedelta
from enum import StrEnum
from typing import Literal

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
    model_assessment: Literal["supported", "weak", "unsupported"]

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


_COVERAGE_ADVISORY = (
    "Coverage labels are model-assisted review signals, not mathematical guarantees."
)


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
    advisory: str = _COVERAGE_ADVISORY


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
        citation_aliases = {
            alias: citation.id
            for citation in artifact.citations
            for alias in (citation.source_id, citation.source_url)
            if alias
        }
        checked: list[CheckedClaim] = []
        omitted_claims = 0
        for claim in proposed.claims:
            claim = claim.model_copy(
                update={
                    "citation_ids": [
                        citation_aliases.get(citation_id, citation_id)
                        for citation_id in claim.citation_ids
                    ]
                }
            )
            unknown = [citation_id for citation_id in claim.citation_ids if citation_id not in citations]
            if unknown:
                raise EvidenceCoverageError(f"Unknown evidence IDs: {', '.join(unknown)}")
            try:
                start, end = _claim_span(artifact.content, claim)
            except EvidenceCoverageError:
                omitted_claims += 1
                continue
            status, explanation = _support_status(claim, citations, now, self.stale_after)
            payload = claim.model_dump(exclude={"model_assessment"})
            payload.update(start=start, end=end)
            checked.append(
                CheckedClaim(
                    **payload,
                    status=status,
                    explanation=explanation,
                )
            )
        if proposed.claims and not checked:
            raise EvidenceCoverageError("Model did not return any exact artifact claims")
        advisory = _COVERAGE_ADVISORY
        if omitted_claims:
            proposal_label = "proposal was" if omitted_claims == 1 else "proposals were"
            advisory += (
                f" {omitted_claims} malformed model {proposal_label} omitted because the text "
                "did not occur exactly in this artifact."
            )
        return EvidenceCoverageReport(
            artifact_id=artifact.id,
            artifact_revision=artifact.revision,
            checked_at=now,
            claims=checked,
            advisory=advisory,
        )


def _claim_span(content: str, claim: ProposedClaim) -> tuple[int, int]:
    if claim.end <= len(content) and content[claim.start : claim.end] == claim.text:
        return claim.start, claim.end
    matches = list(re.finditer(re.escape(claim.text), content))
    if len(matches) != 1:
        raise EvidenceCoverageError(f"Invalid span for claim: {claim.text}")
    match = matches[0]
    return match.start(), match.end()


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
