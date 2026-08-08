"use client";

import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";

import { apiUrl } from "../../lib/api";
import { WorkflowRun as Run } from "./types";

export function RunResult({ run }: { run: Run }) {
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
        <a href={apiUrl(`/runs/${run.id}/trace`)} target="_blank" rel="noreferrer">
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

export function approvalId(value: unknown): string | null {
  if (!value || typeof value !== "object" || Array.isArray(value)) return null;
  const id = (value as Record<string, unknown>).approval_id;
  return typeof id === "string" ? id : null;
}

export function friendlyRunStatus(status: string): string {
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

export function readableRunError(error: unknown): string {
  if (typeof error !== "string" || !error.trim()) return "the workflow could not complete.";
  return error.replace(/^[A-Za-z][A-Za-z0-9]*(?:Error|Rejected):\s*/, "");
}
