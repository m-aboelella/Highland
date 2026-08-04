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
    title: "Northwind success plan",
    passage: "Capacity review is due August 7.",
    source_system: "archive",
    updated_at: "2026-07-01T00:00:00Z",
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

  it("explains the saved copy and renders a formatted document preview", () => {
    render(<ArtifactEditor initialArtifact={artifact} />);

    expect(screen.getByText(/new saved document—not another discovery run/i)).toBeInTheDocument();
    expect(screen.getByLabelText("Editor view")).toBeInTheDocument();
    expect(screen.getByText("Document preview")).toBeInTheDocument();
    expect(screen.getAllByRole("heading", { name: "Summary" })).toHaveLength(1);

    fireEvent.click(screen.getByRole("button", { name: "Preview" }));
    expect(screen.queryByLabelText("Artifact Markdown")).not.toBeInTheDocument();
    expect(screen.getByText("Formatted reading view; PDF pages may differ")).toBeInTheDocument();
  });

  it("explains evidence IDs and links preview markers to readable source cards", () => {
    render(<ArtifactEditor initialArtifact={artifact} />);

    expect(screen.getByText(/E1 is “Evidence 1,” not a user/i)).toBeInTheDocument();
    expect(screen.getByRole("heading", { name: "Northwind success plan" })).toBeInTheDocument();
    expect(screen.getByText("Capacity review is due August 7.")).toBeInTheDocument();
    expect(screen.getAllByRole("link", { name: "E1" })[0]).toHaveAttribute("href", "#evidence-E1");
    expect(screen.getByRole("button", { name: "Insert [E1] at cursor" })).toBeInTheDocument();
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
    fireEvent.change(screen.getByLabelText("What should change?"), {
      target: { value: "Make it concise" },
    });
    fireEvent.click(screen.getByRole("button", { name: "Preview suggested change" }));

    expect(await screen.findByLabelText("Section revision preview")).toHaveTextContent("Revised E1.");
    expect(screen.getByRole("button", { name: "Apply and save section" })).toBeInTheDocument();
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

  it("keeps an edit recoverable when saving fails", async () => {
    vi.stubGlobal("fetch", vi.fn().mockRejectedValue(new Error("offline")));
    render(<ArtifactEditor initialArtifact={artifact} />);

    fireEvent.change(screen.getByLabelText("Artifact Markdown"), {
      target: { value: "Unsaved but recoverable" },
    });
    fireEvent.click(screen.getByRole("button", { name: "Save revision" }));

    expect(await screen.findByRole("alert")).toHaveTextContent("offline");
    expect(screen.getByText("unsaved")).toBeInTheDocument();
    expect(screen.getByLabelText("Artifact Markdown")).toHaveValue("Unsaved but recoverable");
  });

  it("explains claims without mapped evidence after rerunning support review", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue({
      ok: true,
      json: async () => ({
        advisory: "Coverage labels are model-assisted review signals, not guarantees.",
        claims: [{
          text: "Revenue fell.",
          status: "unsupported",
          explanation: "No valid supporting evidence was mapped.",
        }],
      }),
    }));
    render(<ArtifactEditor initialArtifact={artifact} />);
    fireEvent.click(screen.getByRole("button", { name: "Review saved claims" }));
    expect(await screen.findByLabelText("Evidence coverage")).toHaveTextContent("Revenue fell.");
    expect(screen.getByText("No saved evidence mapped")).toBeInTheDocument();
    expect(screen.getByText(/does not mean the source itself is unsupported/i)).toBeInTheDocument();
  });

  it("requires saving draft edits before reviewing their claim support", () => {
    render(<ArtifactEditor initialArtifact={artifact} />);

    fireEvent.change(screen.getByLabelText("Artifact Markdown"), {
      target: { value: "Unsaved claim" },
    });

    expect(screen.getByRole("button", { name: "Review saved claims" })).toBeDisabled();
    expect(screen.getByText("Save this revision before reviewing its claims.")).toBeInTheDocument();
  });
});
