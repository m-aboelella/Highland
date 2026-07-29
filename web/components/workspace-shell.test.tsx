import { render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import { WorkspaceShell } from "./workspace-shell";

vi.mock("next/navigation", () => ({
  usePathname: () => "/artifacts",
}));

vi.stubGlobal("fetch", vi.fn(() => new Promise(() => undefined)));

describe("WorkspaceShell", () => {
  it("shows single-workspace navigation and operational status", () => {
    render(<WorkspaceShell><p>Workspace content</p></WorkspaceShell>);

    for (const label of ["New chat", "Search", "Artifacts", "Agents", "Automations"]) {
      expect(screen.getByRole("link", { name: label })).toBeInTheDocument();
    }
    expect(screen.getByRole("link", { name: "Search" })).toHaveAttribute("href", "/search");
    expect(screen.getByRole("link", { name: "Artifacts" })).toHaveAttribute(
      "aria-current",
      "page",
    );
    expect(screen.getByText("Index checking")).toBeInTheDocument();
    expect(screen.getByText("scripted")).toBeInTheDocument();
    expect(screen.getByText("Single local workspace")).toBeInTheDocument();
    expect(screen.queryByText(/sign in|account|organization/i)).not.toBeInTheDocument();
  });
});
