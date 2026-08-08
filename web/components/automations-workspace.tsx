"use client";

import { useEffect, useState } from "react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";

type Node = { id: string; name: string; kind: string };
type Workflow = {
  id: string;
  name: string;
  description: string;
  nodes: Node[];
  edges: Array<{ source: string; target: string }>;
};
type Run = {
  id: string;
  workflow_id: string;
  workflow_version: number;
  status: string;
  error?: string | null;
  nodes: Record<string, {
    node_id?: string;
    status: string;
    output?: unknown;
    error?: string | null;
    duration_ms?: number | null;
  }>;
  model_calls?: number;
  tool_calls?: number;
  started_at?: string;
  updated_at?: string;
};

const api = process.env.NEXT_PUBLIC_HIGHLAND_API_URL ?? "http://127.0.0.1:8080";

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
    fetch(`${api}/workflow-runs`)
      .then((response) => {
        if (!response.ok) throw new Error("Run history could not be loaded.");
        return response.json();
      })
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
      const response = await fetch(`${api}/workflows/draft`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ goal }),
      });
      const payload = await response.json().catch(() => ({}));
      if (!response.ok) throw new Error(payload.detail ?? "Planning failed.");
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
      const response = await fetch(`${api}/workflows`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ workflow }),
      });
      const payload = await response.json().catch(() => ({}));
      if (!response.ok) {
        throw new Error(payload.detail ?? "The workflow draft could not be saved.");
      }
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
      const response = await fetch(`${api}/workflows/${workflow.id}/publish`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ workflow }),
      });
      const payload = await response.json().catch(() => ({}));
      if (!response.ok) throw new Error(payload.detail ?? "The workflow could not be published.");
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
      const response = await fetch(`${api}/workflows/${workflow.id}/runs`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ test: true, workflow }),
      });
      const run = await response.json().catch(() => ({}));
      if (!response.ok) throw new Error(testRunRequestError(run));
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
      setMessage(caught instanceof Error ? caught.message : "Test run failed.");
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

function RunResult({ run }: { run: Run }) {
  const output = customerOutput(run);
  const completed = Object.values(run.nodes).filter((node) => node.status === "completed").length;
  const total = Object.keys(run.nodes).length;
  const failedNode = Object.values(run.nodes).find((node) => node.status === "failed");

  return (
    <section className={`run-result run-result-${run.status}`} aria-label="Test run result">
      <div className="run-result-intro">
        <p className="eyebrow">{runOutcomeLabel(run.status)}</p>
        <h3>{runOutcomeTitle(run, output)}</h3>
        <p>{runBenefit(run.status, Boolean(output))}</p>
      </div>

      <dl className="run-result-metrics" aria-label="Test run summary">
        <div>
          <dt>Steps completed</dt>
          <dd>{completed} of {total}</dd>
        </div>
        <div>
          <dt>Data checks</dt>
          <dd>{run.tool_calls ?? 0}</dd>
        </div>
        <div>
          <dt>AI summaries</dt>
          <dd>{run.model_calls ?? 0}</dd>
        </div>
        <div>
          <dt>Elapsed time</dt>
          <dd>{runDuration(run)}</dd>
        </div>
      </dl>

      {output !== null && (
        <section className="customer-preview">
          <header>
            <div>
              <small>Customer preview</small>
              <h4>What the automation would produce</h4>
            </div>
            {run.workflow_version === 0 && <span>Safe draft · nothing published</span>}
          </header>
          <PreviewValue value={output} />
        </section>
      )}

      {(run.error || failedNode?.error) && (
        <div className="run-result-error">
          <strong>What needs attention</strong>
          <p>{readableRunError(run.error ?? failedNode?.error)}</p>
        </div>
      )}

      <details className="run-technical-details">
        <summary>Technical details</summary>
        <p>Use these details when debugging or sharing the run with an engineer.</p>
        <ol>
          {Object.entries(run.nodes).map(([nodeId, node]) => (
            <li key={nodeId}>
              <span>{friendlyNodeName(node.node_id ?? nodeId)}</span>
              <b>{friendlyNodeStatus(node.status)}</b>
              <small>{formatNodeDuration(node.duration_ms)}</small>
            </li>
          ))}
        </ol>
        <a href={`${api}/runs/${run.id}/trace`} target="_blank" rel="noreferrer">
          Open raw JSON trace
        </a>
      </details>
    </section>
  );
}

function PreviewValue({ value, depth = 0 }: { value: unknown; depth?: number }) {
  if (typeof value === "string") {
    return (
      <div className="markdown-answer customer-preview-content">
        <ReactMarkdown remarkPlugins={[remarkGfm]}>{value}</ReactMarkdown>
      </div>
    );
  }
  if (Array.isArray(value)) {
    return (
      <ul className="structured-preview">
        {value.slice(0, 8).map((item, index) => (
          <li key={index}><PreviewValue value={item} depth={depth + 1} /></li>
        ))}
        {value.length > 8 && <li>And {value.length - 8} more…</li>}
      </ul>
    );
  }
  if (value && typeof value === "object") {
    const entries = Object.entries(value as Record<string, unknown>);
    if (depth >= 3) return <span>{entries.length} details</span>;
    return (
      <dl className="structured-preview">
        {entries.map(([name, item]) => (
          <div key={name}>
            <dt>{friendlyNodeName(name)}</dt>
            <dd><PreviewValue value={item} depth={depth + 1} /></dd>
          </div>
        ))}
      </dl>
    );
  }
  return <span>{value === null || value === undefined ? "Not provided" : String(value)}</span>;
}

function customerOutput(run: Run): unknown | null {
  if (!run.model_calls) return null;
  const outputs = Object.values(run.nodes)
    .filter((node) => node.status === "completed" && meaningfulOutput(node.output))
    .map((node) => node.output);
  return outputs.at(-1) ?? null;
}

function meaningfulOutput(value: unknown): boolean {
  if (typeof value === "string") return Boolean(value.trim());
  if (Array.isArray(value)) return value.length > 0;
  return Boolean(value && typeof value === "object" && Object.keys(value).length);
}

function approvalId(value: unknown): string | null {
  if (!value || typeof value !== "object" || Array.isArray(value)) return null;
  const id = (value as Record<string, unknown>).approval_id;
  return typeof id === "string" ? id : null;
}

function friendlyRunStatus(status: string): string {
  return status === "completed" ? "Test passed" : status === "paused" ? "Approval needed" : status;
}

function runOutcomeLabel(status: string): string {
  return status === "completed" ? "Ready to review" : status === "paused" ? "Waiting safely" : "Needs attention";
}

function runOutcomeTitle(run: Run, output: unknown | null): string {
  if (run.status === "failed") return "This draft needs one fix before it is ready.";
  if (run.status === "paused") return "The workflow stopped before a protected action.";
  return output === null
    ? "Every planned step completed successfully."
    : "The workflow produced a useful customer update.";
}

function runBenefit(status: string, hasOutput: boolean): string {
  if (status === "failed") {
    return "Nothing was published or scheduled. Fix the step below, then test the draft again.";
  }
  if (status === "paused") {
    return "This confirms the safety check works: no protected action continues without approval.";
  }
  return hasOutput
    ? "This confirms Highland can collect the expected customer data and turn it into a readable update before you publish."
    : "This confirms the workflow can complete its planned work before you publish it.";
}

function runDuration(run: Run): string {
  if (!run.started_at || !run.updated_at) return "—";
  return formatDuration(Date.parse(run.updated_at) - Date.parse(run.started_at));
}

function formatNodeDuration(duration?: number | null): string {
  return duration === null || duration === undefined ? "—" : formatDuration(duration);
}

function formatDuration(milliseconds: number): string {
  if (!Number.isFinite(milliseconds) || milliseconds < 0) return "—";
  if (milliseconds < 1000) return `${Math.round(milliseconds)} ms`;
  return `${(milliseconds / 1000).toFixed(milliseconds < 10000 ? 1 : 0)} s`;
}

function friendlyNodeName(value: string): string {
  const words = value.replaceAll("_", " ").replaceAll("-", " ");
  return words.charAt(0).toUpperCase() + words.slice(1);
}

function friendlyNodeStatus(status: string): string {
  return status === "waiting_approval" ? "waiting for approval" : status.replaceAll("_", " ");
}

function readableRunError(error: unknown): string {
  if (typeof error !== "string" || !error.trim()) return "the workflow could not complete.";
  return error.replace(/^[A-Za-z][A-Za-z0-9]*(?:Error|Rejected):\s*/, "");
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
