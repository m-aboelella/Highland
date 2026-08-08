"""Request contracts shared by Highland's HTTP route modules."""

from pydantic import BaseModel, ConfigDict, Field

from highland.artifacts import ArtifactAssistantMessage, ArtifactCitation, ArtifactType
from highland.discover.service import DiscoverFilters
from highland.workflows import PlannerRecord, WorkflowDefinition


class ApiModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class CreateConversationRequest(ApiModel):
    title: str | None = Field(default=None, max_length=200)


class RenameConversationRequest(ApiModel):
    title: str = Field(min_length=1, max_length=200)


class CreateMessageRequest(ApiModel):
    content: str = Field(min_length=1, max_length=100_000)


class CreateRunRequest(CreateMessageRequest):
    filters: DiscoverFilters = Field(default_factory=DiscoverFilters)


class CancelRunRequest(ApiModel):
    reason: str | None = Field(default=None, max_length=500)


class CreateArtifactRequest(ApiModel):
    title: str = Field(min_length=1, max_length=300)
    artifact_type: ArtifactType
    content: str = Field(max_length=1_000_000)
    conversation_id: str
    run_id: str
    message_id: str | None = None
    citations: list[ArtifactCitation] = Field(default_factory=list)


class UpdateArtifactRequest(ApiModel):
    expected_revision: int = Field(ge=1)
    title: str | None = Field(default=None, min_length=1, max_length=300)
    content: str | None = Field(default=None, max_length=1_000_000)
    citations: list[ArtifactCitation] | None = None
    reason: str = Field(default="manual edit", max_length=300)


class GenerateArtifactRequest(ApiModel):
    artifact_type: ArtifactType
    conversation_id: str
    message_id: str
    instructions: str | None = Field(default=None, max_length=20_000)


class ReviseArtifactSectionRequest(ApiModel):
    expected_revision: int = Field(ge=1)
    heading: str = Field(min_length=1, max_length=300)
    instructions: str = Field(min_length=1, max_length=20_000)


class PreviewArtifactAssistantEditRequest(ApiModel):
    expected_revision: int = Field(ge=1)
    instruction: str = Field(min_length=1, max_length=20_000)
    history: list[ArtifactAssistantMessage] = Field(default_factory=list, max_length=12)
    draft_content: str | None = Field(default=None, min_length=1, max_length=1_000_000)


class DraftWorkflowRequest(ApiModel):
    goal: str = Field(min_length=1, max_length=20_000)


class SaveWorkflowRequest(ApiModel):
    workflow: WorkflowDefinition
    planner: PlannerRecord | None = None


class RunWorkflowRequest(ApiModel):
    version: int | None = Field(default=None, ge=1)
    test: bool = True
    trigger: dict[str, object] = Field(default_factory=dict)
    workflow: WorkflowDefinition | None = None


class PublishWorkflowRequest(ApiModel):
    workflow: WorkflowDefinition | None = None


class ScheduleWorkflowRequest(ApiModel):
    version: int = Field(ge=1)
    interval_seconds: int = Field(ge=60)
