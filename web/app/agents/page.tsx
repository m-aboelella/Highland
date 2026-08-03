import { WorkspaceShell } from "../../components/workspace-shell";
import { AgentsWorkspace } from "../../components/agents-workspace";

export default function AgentsPage() {
  return (
    <WorkspaceShell>
      <AgentsWorkspace />
    </WorkspaceShell>
  );
}
