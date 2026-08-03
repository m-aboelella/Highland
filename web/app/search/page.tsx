import { SearchWorkspace } from "../../components/search-workspace";
import { WorkspaceShell } from "../../components/workspace-shell";

export default function SearchPage() {
  return (
    <WorkspaceShell>
      <SearchWorkspace />
    </WorkspaceShell>
  );
}
