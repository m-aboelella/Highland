import { DiscoverWorkspace } from "../../components/discover-workspace";
import { WorkspaceShell } from "../../components/workspace-shell";

export default function SearchPage() {
  return (
    <WorkspaceShell>
      <DiscoverWorkspace />
    </WorkspaceShell>
  );
}
