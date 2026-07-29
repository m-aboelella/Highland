import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { ArtifactsWorkspace } from "./artifacts-workspace";

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
});

describe("ArtifactsWorkspace", () => {
  it("distinguishes loading from a loaded empty workspace", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue({
      ok: true,
      json: async () => [],
    }));

    render(<ArtifactsWorkspace />);

    expect(screen.getByText("Loading artifacts…")).toBeInTheDocument();
    expect(await screen.findByText("No artifacts yet.")).toBeInTheDocument();
    expect(screen.getByText(/Turn into artifact/)).toBeInTheDocument();
  });

  it("shows a recoverable API error instead of an empty workspace", async () => {
    vi.stubGlobal("fetch", vi.fn().mockRejectedValue(new Error("offline")));

    render(<ArtifactsWorkspace />);

    expect(await screen.findByRole("alert")).toHaveTextContent("Artifacts are unavailable.");
    expect(screen.getByRole("button", { name: "Try again" })).toBeInTheDocument();
  });
});
