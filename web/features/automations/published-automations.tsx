import { PublishedWorkflow } from "./types";

export function PublishedAutomations({
  workflows,
  busyWorkflowId,
  onEdit,
  onDelete,
}: {
  workflows: PublishedWorkflow[];
  busyWorkflowId: string | null;
  onEdit: (workflow: PublishedWorkflow) => void;
  onDelete: (workflow: PublishedWorkflow) => void;
}) {
  return (
    <section className="published-automations" aria-label="Published automations">
      <header>
        <div>
          <p className="eyebrow">Live library</p>
          <h2>Published automations</h2>
        </div>
        <span>{workflows.length} total</span>
      </header>
      {workflows.length === 0 ? (
        <p className="published-empty">
          No published automations yet. Run a safe test, review its result, then publish it.
        </p>
      ) : (
        <div className="published-automation-list">
          {workflows.map((workflow) => (
            <article key={workflow.workflow_id}>
              <header>
                <div>
                  <small>Published · version {workflow.latest_version}</small>
                  <h3>{workflow.name}</h3>
                </div>
                <span>
                  {workflow.active_schedule_count
                    ? `${workflow.active_schedule_count} active schedule${workflow.active_schedule_count === 1 ? "" : "s"}`
                    : "Not scheduled"}
                </span>
              </header>
              <p>{workflow.description || "No description provided."}</p>
              <dl>
                <div><dt>Steps</dt><dd>{workflow.step_count}</dd></div>
                <div><dt>Versions</dt><dd>{workflow.version_count}</dd></div>
                <div><dt>Last published</dt><dd>{formatPublishedAt(workflow.published_at)}</dd></div>
              </dl>
              <div className="published-automation-actions">
                <button
                  className="secondary"
                  disabled={busyWorkflowId !== null}
                  onClick={() => onEdit(workflow)}
                >
                  {busyWorkflowId === workflow.workflow_id ? "Opening…" : "Edit draft"}
                </button>
                <button
                  className="danger-button"
                  disabled={busyWorkflowId !== null}
                  onClick={() => onDelete(workflow)}
                >
                  Delete
                </button>
              </div>
            </article>
          ))}
        </div>
      )}
    </section>
  );
}

function formatPublishedAt(value: string): string {
  const timestamp = Date.parse(value);
  if (!Number.isFinite(timestamp)) return "Unknown";
  return new Intl.DateTimeFormat(undefined, {
    dateStyle: "medium",
    timeStyle: "short",
  }).format(timestamp);
}
