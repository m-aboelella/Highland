"use client";

import { useEffect, useState } from "react";
import {
  approvalId,
  friendlyRunStatus,
  readableRunError,
  RunResult,
} from "../features/automations/run-result";
import { Workflow, WorkflowRun as Run } from "../features/automations/types";
import { ApiError, requestJson } from "../lib/api";

export function AutomationsWorkspace() {
  const [goal, setGoal] = useState("");
  const [workflow, setWorkflow] = useState<Workflow | null>(null);
  const [versions, setVersions] = useState<number[]>([]);
  const [runs, setRuns] = useState<Run[]>([]);
  const [message, setMessage] = useState("Describe a goal to draft a workflow.");
  const [messageKind, setMessageKind] = useState<"status" | "error">("status");
  const [busy, setBusy] = useState<"draft" | "save" | "publish" | "test" | null>(null);
  const [expandedRunId, setExpandedRunId] = useState<string | null>(null);
  const [publishedFingerprint, setPublishedFingerprint] = useState<string | null>(null);

  useEffect(() => {
    requestJson<Run[]>("/workflow-runs", {}, "Run history could not be loaded.")
      .then(setRuns)
      .catch(() => {
        setMessageKind("error");
        setMessage("Run history is unavailable. Check that the Highland API is running.");
      });
  }, []);

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
      const payload = await requestJson<{ version?: number }>(
        `/workflows/${workflow.id}/publish`,
        {
        method: "POST",
        body: JSON.stringify({ workflow }),
        },
        "The workflow could not be published.",
      );
      if (typeof payload.version !== "number") {
        throw new Error("The workflow was published, but the API did not return its version.");
      }
      setVersions(Array.from({ length: payload.version }, (_, index) => index + 1));
      setPublishedFingerprint(workflowFingerprint(workflow));
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
      const run = await requestJson<Run>(`/workflows/${workflow.id}/runs`, {
        method: "POST",
        body: JSON.stringify({ test: true, workflow }),
      }, "The test run could not be started.");
      setRuns((current) => [run, ...current]);
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
                {expanded && <RunResult run={run} />}
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
    </section>
  );
}

function workflowFingerprint(workflow: Workflow): string {
  return JSON.stringify(workflow);
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
