"use client";

import { useEffect, useState } from "react";

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
  nodes: Record<string, { status: string; output?: { approval_id?: string } }>;
};

const api = process.env.NEXT_PUBLIC_HIGHLAND_API_URL ?? "http://127.0.0.1:8080";

export function AutomationsWorkspace() {
  const [goal, setGoal] = useState("");
  const [workflow, setWorkflow] = useState<Workflow | null>(null);
  const [versions, setVersions] = useState<number[]>([]);
  const [runs, setRuns] = useState<Run[]>([]);
  const [message, setMessage] = useState("Describe a goal to draft a workflow.");
  const [busy, setBusy] = useState<"draft" | "save" | "publish" | "test" | null>(null);

  useEffect(() => {
    fetch(`${api}/workflow-runs`)
      .then((response) => {
        if (!response.ok) throw new Error("Run history could not be loaded.");
        return response.json();
      })
      .then(setRuns)
      .catch(() => setMessage("Run history is unavailable. Check that the Highland API is running."));
  }, []);

  async function draft() {
    setBusy("draft");
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
      setMessage(`Review the model rationale: ${payload.planner.rationale}`);
    } catch (caught) {
      setMessage(caught instanceof Error ? caught.message : "Planning failed.");
    } finally {
      setBusy(null);
    }
  }

  async function save() {
    if (!workflow) return;
    setBusy("save");
    try {
      const response = await fetch(`${api}/workflows`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ workflow }),
      });
      if (!response.ok) throw new Error("The workflow draft could not be saved.");
      setMessage("Draft saved. It is not published or scheduled.");
    } catch (caught) {
      setMessage(caught instanceof Error ? caught.message : "Save failed.");
    } finally {
      setBusy(null);
    }
  }

  async function publish() {
    if (!workflow) return;
    setBusy("publish");
    try {
      const response = await fetch(`${api}/workflows/${workflow.id}/publish`, { method: "POST" });
      const payload = await response.json().catch(() => ({}));
      if (!response.ok) throw new Error(payload.detail ?? "The workflow could not be published.");
      setVersions((current) => [...current, payload.version]);
      setMessage(`Published immutable version ${payload.version}.`);
    } catch (caught) {
      setMessage(caught instanceof Error ? caught.message : "Publish failed.");
    } finally {
      setBusy(null);
    }
  }

  async function testRun() {
    if (!workflow) return;
    setBusy("test");
    try {
      const response = await fetch(`${api}/workflows/${workflow.id}/runs`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ test: true }),
      });
      const run = await response.json().catch(() => ({}));
      if (!response.ok) throw new Error(run.detail ?? "The test run could not be started.");
      setRuns((current) => [run, ...current]);
      setMessage("Test run finished without publishing or scheduling.");
    } catch (caught) {
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
          <p role="status">{message}</p>
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
                <button disabled={busy !== null} onClick={() => void publish()}>
                  {busy === "publish" ? "Publishing…" : "Publish"}
                </button>
              </div>
              <p>Version history: {versions.length ? versions.join(", ") : "No published versions"}</p>
            </>
          )}
        </section>
        <aside className="run-history">
          <h2>Run history & approvals</h2>
          {runs.length === 0 && <p>No workflow runs yet.</p>}
          {runs.map((run) => {
            const approval = Object.values(run.nodes).find((node) => node.output?.approval_id);
            return (
              <article key={run.id}>
                <b>{run.status}</b>
                <span>{run.workflow_id} · version {run.workflow_version || "draft test"}</span>
                <a href={`${api}/runs/${run.id}/trace`}>Inspect exact trace</a>
                {approval?.output?.approval_id && (
                  <div className="approval-card">
                    <strong>Approval required</strong>
                    <code>{approval.output.approval_id}</code>
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
