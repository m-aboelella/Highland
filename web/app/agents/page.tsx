import { WorkspaceShell } from "../../components/workspace-shell";

const profile = {
  name: "General workspace agent",
  model: "Command A+",
  tools: "Read and approved write tools from six local connectors",
  instructions:
    "Verify mutable facts, distinguish observations from hypotheses, and preserve source identifiers.",
  budgets: "10 model calls · 20 steps · 120 seconds",
};

export default function AgentsPage() {
  return (
    <WorkspaceShell>
      <section>
        <p className="eyebrow">Agent profiles</p>
        <h1>General workspace agent</h1>
        <div className="agent-grid">
          {Object.entries(profile).map(([label, value]) => (
            <article className="agent-card" key={label}>
              <h2>{label}</h2>
              <p>{value}</p>
            </article>
          ))}
        </div>
      </section>
    </WorkspaceShell>
  );
}
