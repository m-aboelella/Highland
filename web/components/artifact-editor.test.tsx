import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { ArtifactDocument, ArtifactEditor } from "./artifact-editor";

const artifact: ArtifactDocument = {
  id: "art_1",
  title: "Northwind briefing",
  artifact_type: "briefing",
  content: "## Summary\n\nOriginal [E1].\n\n## Risks\n\nUnchanged [E1].\n",
  citations: [{
    id: "E1",
    label: "1",
    source_id: "doc_1",
    source_url: "mock://archive/doc_1",
  }],
  revision: 1,
  conversation_id: "con_1",
  run_id: "run_1",
  updated_at: "2026-07-29T00:00:00Z",
};

describe("ArtifactEditor", () => {
  beforeEach(() => vi.restoreAllMocks());
  afterEach(cleanup);

  it("persists manual Markdown edits without requesting a model revision", async () => {
    const fetch = vi.fn().mockResolvedValue({
      ok: true,
      json: async () => ({ ...artifact, revision: 2, content: "Manual change" }),
    });
    vi.stubGlobal("fetch", fetch);
    render(<ArtifactEditor initialArtifact={artifact} />);

    fireEvent.change(screen.getByLabelText("Artifact Markdown"), {
      target: { value: "Manual change" },
    });
    expect(screen.getByText("unsaved")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Save revision" }));

    await waitFor(() => expect(screen.getByText("saved")).toBeInTheDocument());
    expect(fetch).toHaveBeenCalledTimes(1);
    expect(fetch.mock.calls[0][0]).toContain("/artifacts/art_1");
    expect(fetch.mock.calls[0][1].method).toBe("PATCH");
  });

  it("previews a section edit before replacing it and keeps provenance visible", async () => {
    const preview = {
      expected_revision: 1,
      heading: "Summary",
      original_markdown: "## Summary\n\nOriginal [E1].",
      proposed_markdown: "## Summary\n\nRevised [E1].",
      resulting_content: "## Summary\n\nRevised [E1].\n\n## Risks\n\nUnchanged [E1].\n",
      citations: artifact.citations,
    };
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue({ ok: true, json: async () => preview }));
    render(<ArtifactEditor initialArtifact={artifact} />);

    fireEvent.change(screen.getByLabelText("Section"), { target: { value: "Summary" } });
    fireEvent.change(screen.getByLabelText("Revision instruction"), {
      target: { value: "Make it concise" },
    });
    fireEvent.click(screen.getByRole("button", { name: "Preview section edit" }));

    expect(await screen.findByLabelText("Section revision preview")).toHaveTextContent("Revised [E1]");
    expect(screen.getByRole("button", { name: "Replace this section" })).toBeInTheDocument();
    expect(screen.getByText(/From conversation con_1/)).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "View originating run" })).toHaveAttribute(
      "href",
      expect.stringContaining("/runs/run_1/trace"),
    );
  });

  it("asks before closing with unsaved changes", () => {
    const close = vi.fn();
    const confirm = vi.spyOn(window, "confirm").mockReturnValue(false);
    render(<ArtifactEditor initialArtifact={artifact} onClose={close} />);
    fireEvent.change(screen.getByLabelText("Artifact Markdown"), {
      target: { value: "Unsaved" },
    });
    fireEvent.click(screen.getByRole("button", { name: "Close editor" }));
    expect(confirm).toHaveBeenCalled();
    expect(close).not.toHaveBeenCalled();
  });
});
