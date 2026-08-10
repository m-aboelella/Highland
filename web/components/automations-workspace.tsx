"use client";

import { useEffect, useState } from "react";
import {
  approvalId,
  friendlyRunStatus,
  readableRunError,
  RunResult,
} from "../features/automations/run-result";
import { PublishedAutomations } from "../features/automations/published-automations";
import {
  PublishedWorkflow,
  Workflow,
  WorkflowRun as Run,
  WorkflowVersion,
} from "../features/automations/types";
import { ApiError, requestJson } from "../lib/api";

export function AutomationsWorkspace() {
  const [goal, setGoal] = useState("");
  const [workflow, setWorkflow] = useState<Workflow | null>(null);
  const [versions, setVersions] = useState<number[]>([]);
  const [runs, setRuns] = useState<Run[]>([]);
  const [publishedWorkflows, setPublishedWorkflows] = useState<PublishedWorkflow[]>([]);
  const [message, setMessage] = useState("Describe a goal to draft a workflow.");
  const [messageKind, setMessageKind] = useState<"status" | "error">("status");
  const [busy, setBusy] = useState<"draft" | "save" | "publish" | "test" | null>(null);
  const [expandedRunId, setExpandedRunId] = useState<string | null>(null);
  const [publishedFingerprint, setPublishedFingerprint] = useState<string | null>(null);
  const [publishingRunId, setPublishingRunId] = useState<string | null>(null);
  const [busyWorkflowId, setBusyWorkflowId] = useState<string | null>(null);

  useEffect(() => {
    requestJson<Run[]>("/workflow-runs", {}, "Run history could not be loaded.")
      .then(setRuns)
      .catch(() => {
        setMessageKind("error");
        setMessage("Run history is unavailable. Check that the Highland API is running.");
      });
    requestJson<PublishedWorkflow[]>(
      "/published-workflows",
      {},
      "Published automations could not be loaded.",
    )
      .then(setPublishedWorkflows)
      .catch(() => {
        setMessageKind("error");
        setMessage("Published automations are unavailable. Check that the Highland API is running.");
      });
  }, []);

  function rememberPublication(version: WorkflowVersion) {
    setPublishedWorkflows((current) => {
      const existing = current.find((item) => item.workflow_id === version.workflow_id);
      const summary: PublishedWorkflow = {
        workflow_id: version.workflow_id,
        name: version.definition.name,
        description: version.definition.description,
        latest_version: version.version,
        version_count: Math.max(existing?.version_count ?? 0, version.version),
        published_at: version.published_at,
        step_count: version.definition.nodes.length,
        active_schedule_count: existing?.active_schedule_count ?? 0,
      };
      return [summary, ...current.filter((item) => item.workflow_id !== summary.workflow_id)];
    });
  }

  async function draft() {
    setBusy("draft");
    setMessageKind("status");
    setMessage("Planning with the configured model…");
    try {
      const payload = await requestJson<{
        workflow: Workflow;
        planner: { rationale: string };
      }>("/workflows/draft", {
        method: "POST",
        body: JSON.stringify({ goal }),
      }, "Planning failed.");
      setWorkflow(payload.workflow);
      setVersions([]);
      setPublishedFingerprint(null);
      setMessageKind("status");
      setMessage(`Review the model rationale: ${payload.planner.rationale}`);
    } catch (caught) {
      setMessageKind("error");
      setMessage(caught instanceof Error ? caught.message : "Planning failed.");
    } finally {
      setBusy(null);
    }
  }

  async function save() {
    if (!workflow) return;
    setBusy("save");
    setMessageKind("status");
    setMessage("Saving the current draft…");
    try {
      await requestJson<Workflow>("/workflows", {
        method: "POST",
        body: JSON.stringify({ workflow }),
      }, "The workflow draft could not be saved.");
      setMessageKind("status");
      setMessage("Draft saved. It is not published or scheduled.");
    } catch (caught) {
      setMessageKind("error");
      setMessage(caught instanceof Error ? caught.message : "Save failed.");
    } finally {
      setBusy(null);
    }
  }

  async function publish() {
    if (!workflow) return;
    setBusy("publish");
    setMessageKind("status");
    setMessage("Saving and publishing the current reviewed draft…");
    try {
      const payload = await requestJson<WorkflowVersion>(
        `/workflows/${workflow.id}/publish`,
        {
          method: "POST",
          body: JSON.stringify({ workflow }),
        },
        "The workflow could not be published.",
      );
      if (typeof payload.version !== "number" || !payload.definition) {
        throw new Error("The workflow was published, but the API did not return its version.");
      }
      setVersions(Array.from({ length: payload.version }, (_, index) => index + 1));
      setPublishedFingerprint(workflowFingerprint(workflow));
      rememberPublication(payload);
      setMessageKind("status");
      setMessage(
        `Version ${payload.version} is published and locked. Scheduling has not been activated.`
      );
    } catch (caught) {
      setMessageKind("error");
      setMessage(caught instanceof Error ? caught.message : "Publish failed.");
    } finally {
      setBusy(null);
    }
  }

  const latestVersion = versions.at(-1);
  const currentDraftIsPublished = Boolean(
    workflow && publishedFingerprint === workflowFingerprint(workflow)
  );

  async function testRun() {
    if (!workflow) return;
    setBusy("test");
    setMessageKind("status");
    setMessage("Saving the draft and running a safe test…");
    try {
      const created = await requestJson<Run>(`/workflows/${workflow.id}/runs`, {
        method: "POST",
        body: JSON.stringify({ test: true, workflow }),
      }, "The test run could not be started.");
      const run = await requestJson<Run>(
        `/workflow-runs/${created.id}`,
        {},
        "The test completed, but its persisted result could not be loaded.",
      );
      setRuns((current) => [run, ...current.filter((item) => item.id !== run.id)]);
      setExpandedRunId(run.id);
      if (run.status === "failed") {
        setMessageKind("error");
        setMessage(`Test run failed: ${readableRunError(run.error)} Open the result to see what needs attention.`);
      } else if (run.status === "paused") {
        setMessageKind("status");
        setMessage("Test run paused for approval. The draft was saved but not published or scheduled.");
      } else {
        setMessageKind("status");
        setMessage("Test run completed. The draft was saved but not published or scheduled.");
      }
    } catch (caught) {
      setMessageKind("error");
      setMessage(
        caught instanceof ApiError
          ? testRunRequestError(caught.payload)
          : caught instanceof Error
            ? caught.message
            : "Test run failed.",
      );
    } finally {
      setBusy(null);
    }
  }

  async function publishTestRun(run: Run) {
    setPublishingRunId(run.id);
    setMessageKind("status");
    setMessage("Publishing the exact workflow that produced this test result…");
    try {
      const published = await requestJson<WorkflowVersion>(
        `/workflow-runs/${run.id}/publish`,
        { method: "POST" },
        "This test run could not be published.",
      );
      rememberPublication(published);
      setRuns((current) => current.map((item) => (
        item.id === run.id ? { ...item, published_version: published.version } : item
      )));
      if (workflow?.id === published.workflow_id) {
        setVersions(Array.from({ length: published.version }, (_, index) => index + 1));
        setPublishedFingerprint(workflowFingerprint(published.definition));
      }
      setMessageKind("status");
      setMessage(
        `Test approved and published as version ${published.version}. Scheduling has not been activated.`
      );
    } catch (caught) {
      setMessageKind("error");
      setMessage(caught instanceof Error ? caught.message : "Publish from test failed.");
    } finally {
      setPublishingRunId(null);
    }
  }

  async function editPublished(selected: PublishedWorkflow) {
    setBusyWorkflowId(selected.workflow_id);
    setMessageKind("status");
    setMessage(`Opening ${selected.name} as an editable draft…`);
    try {
      const payload = await requestJson<{
        draft: Workflow;
        versions: WorkflowVersion[];
      }>(
        `/workflows/${selected.workflow_id}`,
        {},
        "The published automation could not be opened.",
      );
      const latest = payload.versions.at(-1);
      setWorkflow(payload.draft);
      setVersions(payload.versions.map((version) => version.version));
      setPublishedFingerprint(
        latest ? workflowFingerprint(latest.definition) : null
      );
      setMessageKind("status");
      setMessage(
        `Editing ${selected.name}. Publish changes when the updated draft is ready.`
      );
    } catch (caught) {
      setMessageKind("error");
      setMessage(caught instanceof Error ? caught.message : "The automation could not be opened.");
    } finally {
      setBusyWorkflowId(null);
    }
  }

  async function deletePublished(selected: PublishedWorkflow) {
    const confirmed = window.confirm(
      `Delete ${selected.name}? Its draft, published versions, and schedules will be removed. Run history will be kept.`
    );
    if (!confirmed) return;
    setBusyWorkflowId(selected.workflow_id);
    setMessageKind("status");
    setMessage(`Deleting ${selected.name}…`);
    try {
      await requestJson<void>(
        `/workflows/${selected.workflow_id}`,
        { method: "DELETE" },
        "The published automation could not be deleted.",
      );
      setPublishedWorkflows((current) => (
        current.filter((item) => item.workflow_id !== selected.workflow_id)
      ));
      if (workflow?.id === selected.workflow_id) {
        setWorkflow(null);
        setVersions([]);
        setPublishedFingerprint(null);
      }
      setMessageKind("status");
      setMessage(`${selected.name} was deleted. Its run history is still available.`);
    } catch (caught) {
      setMessageKind("error");
      setMessage(caught instanceof Error ? caught.message : "Delete failed.");
    } finally {
      setBusyWorkflowId(null);
    }
  }

  return (
    <section className="automation-workspace">
      <p className="eyebrow">Automate</p>
      <h1>Build a small, inspectable workflow.</h1>
      <div className="automation-grid">
        <section className="workflow-plan">
          <label htmlFor="workflow-goal">Natural-language goal</label>
          <textarea
            id="workflow-goal"
            value={goal}
            onChange={(event) => setGoal(event.target.value)}
            placeholder="Every Monday, review active enterprise customer health…"
          />
          <button onClick={() => void draft()} disabled={!goal.trim() || busy !== null}>
            {busy === "draft" ? "Drafting…" : "Draft plan"}
          </button>
          <p role={messageKind === "error" ? "alert" : "status"}>{message}</p>
          {workflow && (
            <>
              <header>
                <div>
                  <small>Editable draft</small>
                  <h2>{workflow.name}</h2>
                </div>
                <span>Model · configured planner</span>
              </header>
              <label>
                Automation name
                <input
                  aria-label="Automation name"
                  value={workflow.name}
                  onChange={(event) => setWorkflow({ ...workflow, name: event.target.value })}
                />
              </label>
              <label>
                Description
                <textarea
                  className="workflow-description"
                  aria-label="Automation description"
                  value={workflow.description}
                  onChange={(event) => setWorkflow({
                    ...workflow,
                    description: event.target.value,
                  })}
                />
              </label>
              <label>
                Estimated budget
                <input aria-label="Estimated budget" defaultValue="10 model · 25 tool calls" />
              </label>
              <ol className="node-editor" aria-label="Workflow nodes">
                {workflow.nodes.map((node) => (
                  <li key={node.id}>
                    <b>{node.kind}</b>
                    <input
                      aria-label={`${node.id} name`}
                      value={node.name}
                      onChange={(event) => setWorkflow({
                        ...workflow,
                        nodes: workflow.nodes.map((item) =>
                          item.id === node.id ? { ...item, name: event.target.value } : item),
                      })}
                    />
                    <code>{node.id}</code>
                  </li>
                ))}
              </ol>
              <div className="workflow-actions">
                <button className="secondary" disabled={busy !== null} onClick={() => void save()}>
                  {busy === "save" ? "Saving…" : "Save draft"}
                </button>
                <button className="secondary" disabled={busy !== null} onClick={() => void testRun()}>
                  {busy === "test" ? "Running…" : "Test run"}
                </button>
                <button
                  disabled={busy !== null || currentDraftIsPublished}
                  onClick={() => void publish()}
                >
                  {busy === "publish"
                    ? "Publishing…"
                    : currentDraftIsPublished
                      ? `Published v${latestVersion}`
                      : versions.length
                        ? "Publish changes"
                        : "Publish"}
                </button>
              </div>
              <p className="publication-note">
                Publishing locks this reviewed draft as a version. It does not activate a schedule.
              </p>
              {latestVersion !== undefined && (
                <section className="publication-card" aria-label="Publication status">
                  <div>
                    <small>Published</small>
                    <strong>Version {latestVersion} is ready</strong>
                  </div>
                  <p>
                    This version is immutable and ready for scheduling. Edit the draft to publish
                    a new version.
                  </p>
                </section>
              )}
              <p>Version history: {versions.length ? versions.join(", ") : "No published versions"}</p>
            </>
          )}
        </section>
        <aside className="run-history">
          <h2>Run history & approvals</h2>
          {runs.length === 0 && <p>No workflow runs yet.</p>}
          {runs.map((run) => {
            const approval = Object.values(run.nodes).find((node) => approvalId(node.output));
            const expanded = expandedRunId === run.id;
            return (
              <article key={run.id}>
                <header className="run-history-header">
                  <div>
                    <b>{friendlyRunStatus(run.status)}</b>
                    <span>{run.workflow_id} · {run.workflow_version ? `version ${run.workflow_version}` : "draft test"}</span>
                  </div>
                  <button
                    className="run-result-toggle"
                    aria-expanded={expanded}
                    onClick={() => setExpandedRunId(expanded ? null : run.id)}
                  >
                    {expanded ? "Hide result" : "View result"}
                  </button>
                </header>
                {expanded && (
                  <RunResult
                    run={run}
                    publishing={publishingRunId === run.id}
                    onPublish={run.test && run.workflow_snapshot
                      ? () => void publishTestRun(run)
                      : undefined}
                  />
                )}
                {approvalId(approval?.output) && (
                  <div className="approval-card">
                    <strong>Approval required</strong>
                    <code>{approvalId(approval?.output)}</code>
                  </div>
                )}
              </article>
            );
          })}
        </aside>
      </div>
      <PublishedAutomations
        workflows={publishedWorkflows}
        busyWorkflowId={busyWorkflowId}
        onEdit={(selected) => void editPublished(selected)}
        onDelete={(selected) => void deletePublished(selected)}
      />
    </section>
  );
}

function workflowFingerprint(workflow: Workflow): string {
  const content = { ...workflow } as Workflow & {
    created_at?: string;
    updated_at?: string;
  };
  delete content.created_at;
  delete content.updated_at;
  return JSON.stringify(content);
}

function testRunRequestError(payload: unknown): string {
  if (payload && typeof payload === "object" && "detail" in payload) {
    const detail = payload.detail;
    if (typeof detail === "string" && detail.trim()) return detail;
    if (Array.isArray(detail)) {
      return "This draft is invalid. Generate a new plan, then try the test run again.";
    }
  }
  return "The test run could not be started.";
}
