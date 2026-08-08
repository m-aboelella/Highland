from __future__ import annotations

from dataclasses import dataclass

from .artifacts import (
    ArtifactAssistant,
    ArtifactGenerator,
    ArtifactRepository,
    EvidenceCoverageChecker,
)
from .discover.conversations import ConversationStore
from .discover.service import DiscoverService
from .models.provider import ModelProvider, build_model_provider
from .retrieval.sources import MCPSourceReader
from .retrieval.sync import IndexSynchronizer
from .runtime.agent import AgentProfile
from .runtime.approvals import ApprovalStore
from .runtime.cancellation import RunCancellationStore
from .runtime.events import RunEventStore
from .runtime.mcp import MCPGateway
from .runtime.policy import ToolRegistry
from .settings import HighlandSettings
from .workflows import (
    WeeklyCustomerHealthRunner,
    WeeklyHealthRunRepository,
    WorkflowDefinition,
    WorkflowExecutor,
    WorkflowRepository,
    WorkflowRun,
    WorkflowRunRepository,
)
from .workflows.schedules import WorkflowScheduleRepository
from .workspace import WorkspacePaths


def _model_ids(provider: ModelProvider) -> dict[str, str]:
    return {
        "chat": str(getattr(provider.chat, "model", provider.chat.name)),
        "embedding": str(getattr(provider.embeddings, "model", provider.embeddings.name)),
        "rerank": str(getattr(provider.rerank, "model", provider.rerank.name)),
    }


def _limits(settings: HighlandSettings, profile: AgentProfile) -> dict[str, dict[str, int | float]]:
    return {
        "agent": {
            "max_steps": profile.budgets.max_steps,
            "max_model_calls": profile.budgets.max_model_calls,
            "max_wall_seconds": profile.budgets.max_wall_seconds,
        },
        "provider": {
            "max_model_calls": settings.max_model_calls_per_run,
            "max_rerank_searches": settings.max_rerank_searches_per_run,
            "max_tokens": settings.max_tokens_per_run,
            "max_cost_usd": settings.max_run_cost_usd,
        },
    }


@dataclass(frozen=True, slots=True)
class ApplicationServices:
    """The application composition root shared by HTTP and evaluation entrypoints."""

    settings: HighlandSettings
    workspace: WorkspacePaths
    provider: ModelProvider
    profile: AgentProfile
    approvals: ApprovalStore
    run_events: RunEventStore
    cancellations: RunCancellationStore
    conversations: ConversationStore
    artifacts: ArtifactRepository
    artifact_assistant: ArtifactAssistant
    artifact_generator: ArtifactGenerator
    coverage_checker: EvidenceCoverageChecker
    workflows: WorkflowRepository
    workflow_runs: WorkflowRunRepository
    workflow_schedules: WorkflowScheduleRepository
    discover: DiscoverService

    @classmethod
    def build(
        cls,
        settings: HighlandSettings,
        *,
        model_provider: ModelProvider | None = None,
    ) -> ApplicationServices:
        workspace = WorkspacePaths.from_root(settings.workspace_dir)
        workspace.ensure()
        provider = model_provider or build_model_provider(settings)
        profile = AgentProfile.load(settings.agent_profile_config)
        approvals = ApprovalStore(workspace.runs / "approvals")
        run_events = RunEventStore(workspace.runs / "events")
        cancellations = RunCancellationStore(workspace.runs / "cancellations")
        conversations = ConversationStore(workspace.conversations)
        artifacts = ArtifactRepository(workspace.artifacts)
        workflows = WorkflowRepository(workspace.workflows)
        return cls(
            settings=settings,
            workspace=workspace,
            provider=provider,
            profile=profile,
            approvals=approvals,
            run_events=run_events,
            cancellations=cancellations,
            conversations=conversations,
            artifacts=artifacts,
            artifact_assistant=ArtifactAssistant(provider.chat, artifacts),
            artifact_generator=ArtifactGenerator(provider.chat, artifacts),
            coverage_checker=EvidenceCoverageChecker(provider.chat),
            workflows=workflows,
            workflow_runs=WorkflowRunRepository(workspace.runs / "workflows"),
            workflow_schedules=WorkflowScheduleRepository(workspace.workflows / "schedules"),
            discover=DiscoverService(
                index_dir=workspace.indexes / "search",
                conversations=conversations,
                runs_dir=workspace.runs,
                provider=provider,
                profile=profile,
                tool_policy=settings.tool_policy_config,
                connector_commands=settings.connector_commands,
                connector_timeout_seconds=settings.connector_timeout_seconds,
                trace_context={
                    "models": _model_ids(provider),
                    "limits": _limits(settings, profile),
                },
            ),
        )

    def synchronizer(self) -> IndexSynchronizer:
        return IndexSynchronizer(
            MCPSourceReader(
                self.settings.connector_commands,
                timeout_seconds=self.settings.connector_timeout_seconds,
            ),
            index_dir=self.workspace.indexes / "search",
            reports_dir=self.workspace.synchronization,
            embedding_model=self.provider.embeddings,
        )

    def effective_models(self) -> dict[str, str]:
        return _model_ids(self.provider)

    def effective_limits(self) -> dict[str, dict[str, int | float]]:
        return _limits(self.settings, self.profile)

    def workflow_executor(self, tools: ToolRegistry) -> WorkflowExecutor:
        """Build the same workflow runtime used by HTTP and evaluation entrypoints."""
        return WorkflowExecutor(
            model=self.provider.chat,
            tools=tools,
            repository=self.workflow_runs,
            approvals=self.approvals,
        )

    def weekly_health_runner(self, tools: ToolRegistry) -> WeeklyCustomerHealthRunner:
        """Build the canonical weekly-health workflow with production services."""
        return WeeklyCustomerHealthRunner(
            model=self.provider.chat,
            tools=tools,
            artifacts=self.artifacts,
            runs=WeeklyHealthRunRepository(self.workspace.runs / "weekly-health"),
            events=self.run_events,
        )

    async def execute_workflow(
        self,
        definition: WorkflowDefinition,
        *,
        run_id: str,
        workflow_version: int,
        trigger: dict[str, object],
        test: bool,
    ) -> WorkflowRun:
        """Execute and trace a workflow through the production MCP boundary."""
        self.run_events.append(
            run_id,
            "run_started",
            {
                "workflow_id": definition.id,
                "workflow_version": workflow_version,
                "test": test,
            },
        )
        async with MCPGateway(
            self.settings.connector_commands,
            startup_timeout_seconds=self.settings.connector_timeout_seconds,
            request_timeout_seconds=self.settings.connector_timeout_seconds,
        ) as gateway:
            registry = ToolRegistry.from_file(gateway, self.settings.tool_policy_config)
            run = await self.workflow_executor(registry).run(
                definition,
                run_id=run_id,
                workflow_version=workflow_version,
                trigger=trigger,
            )
        node_kinds = {node.id: node.kind for node in definition.nodes}
        for node in run.nodes.values():
            self.run_events.append(
                run_id,
                "model_call" if node_kinds[node.node_id] == "generate" else "tool_call",
                {
                    "node_id": node.node_id,
                    "status": node.status.value,
                    "attempts": node.attempts,
                    "duration_ms": node.duration_ms,
                    "usage": node.usage.model_dump(mode="json"),
                },
            )
        terminal_event = {
            "completed": "run_completed",
            "paused": "approval_required",
        }.get(run.status.value, "run_failed")
        self.run_events.append(
            run_id,
            terminal_event,
            {
                "workflow_id": definition.id,
                "workflow_version": workflow_version,
                "status": run.status.value,
            },
        )
        return run
