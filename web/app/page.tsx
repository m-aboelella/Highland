import { WorkspaceShell } from "../components/workspace-shell";
import { DiscoverWorkspace } from "../components/discover-workspace";

export default function Home() {
  return (
    <WorkspaceShell>
      <DiscoverWorkspace />
    </WorkspaceShell>
  );
}
