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

  useEffect(() => {
    fetch(`${api}/workflow-runs`)
      .then((response) => response.ok ? response.json() : [])
      .then(setRuns)
      .catch(() => undefined);
  }, []);

  async function draft() {
    setMessage("Planning with the configured model…");
    const response = await fetch(`${api}/workflows/draft`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ goal }),
    });
    const payload = await response.json();
    if (!response.ok) return setMessage(payload.detail ?? "Planning failed.");
    setWorkflow(payload.workflow);
    setMessage(`Review the model rationale: ${payload.planner.rationale}`);
  }

  async function save() {
    if (!workflow) return;
    const response = await fetch(`${api}/workflows`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ workflow }),
    });
    setMessage(response.ok ? "Draft saved. It is not published or scheduled." : "Save failed.");
  }

  async function publish() {
    if (!workflow) return;
    const response = await fetch(`${api}/workflows/${workflow.id}/publish`, { method: "POST" });
    const payload = await response.json();
    if (response.ok) {
      setVersions((current) => [...current, payload.version]);
      setMessage(`Published immutable version ${payload.version}.`);
    }
  }

  async function testRun() {
    if (!workflow) return;
    const response = await fetch(`${api}/workflows/${workflow.id}/runs`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ test: true }),
    });
    const run = await response.json();
    if (response.ok) {
      setRuns((current) => [run, ...current]);
      setMessage("Test run finished without publishing or scheduling.");
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
          <button onClick={draft} disabled={!goal.trim()}>Draft plan</button>
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
                <button className="secondary" onClick={save}>Save draft</button>
                <button className="secondary" onClick={testRun}>Test run</button>
                <button onClick={publish}>Publish</button>
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
