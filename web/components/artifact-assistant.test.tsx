import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { ArtifactAssistantPanel } from "./artifact-assistant";
import { ArtifactDocument } from "./artifact-editor";

const artifact: ArtifactDocument = {
  id: "art_1",
  title: "Northwind briefing",
  artifact_type: "briefing",
  content: "## Summary\n\nOriginal [E1].\n",
  citations: [{
    id: "E1",
    label: "1",
    source_id: "doc_1",
    source_url: "mock://archive/doc_1",
    title: "Northwind success plan",
    passage: "Capacity review is due August 7.",
    source_system: "archive",
  }],
  revision: 1,
  conversation_id: "con_1",
  run_id: "run_1",
  updated_at: "2026-07-29T00:00:00Z",
};

describe("ArtifactAssistantPanel", () => {
  beforeEach(() => vi.restoreAllMocks());
  afterEach(cleanup);

  it("refines one working draft across chat turns before writing", async () => {
    const firstDraft = "## Summary\n\nOriginal [E1].\n\n## Next step\n\nReview capacity by August 7 [E1].\n";
    const refinedDraft = "## Summary\n\nOriginal [E1].\n\n## Next step\n\nReview by August 7 [E1].\n";
    const fetch = vi.fn().mockResolvedValueOnce({
      ok: true,
      json: async () => ({
        artifact_id: "art_1",
        expected_revision: 1,
        assistant_message: "Added a clearer next step.",
        summary: "Added a clearer next step.",
        proposed_content: firstDraft,
        citations: artifact.citations,
        model: "command-a-03-2025",
        operations: [
          { tool: "read_artifact", label: "Read the Markdown file", detail: "Opened revision 1.", status: "completed" },
          { tool: "read_saved_evidence", label: "Read saved evidence", detail: "Read E1.", status: "completed" },
          { tool: "propose_markdown_edit", label: "Propose a Markdown change", detail: "Added a next step.", status: "completed" },
          { tool: "write_artifact_revision", label: "Write a new revision", detail: "Waiting for approval.", status: "awaiting_approval" },
        ],
      }),
    }).mockResolvedValueOnce({
      ok: true,
      json: async () => ({
        artifact_id: "art_1",
        expected_revision: 1,
        assistant_message: "Made our working draft more concise.",
        summary: "Made our working draft more concise.",
        proposed_content: refinedDraft,
        citations: artifact.citations,
        model: "command-a-03-2025",
        operations: [
          { tool: "read_artifact", label: "Read the working draft", detail: "Opened the conversational draft.", status: "completed" },
          { tool: "propose_markdown_edit", label: "Propose a Markdown change", detail: "Shortened the next step.", status: "completed" },
          { tool: "write_artifact_revision", label: "Write a new revision", detail: "Waiting for approval.", status: "awaiting_approval" },
        ],
      }),
    });
    vi.stubGlobal("fetch", fetch);
    const onApply = vi.fn().mockResolvedValue({ ...artifact, revision: 2 });
    render(<ArtifactAssistantPanel artifact={artifact} onApply={onApply} saveState="saved" />);

    expect(screen.getByText(/cannot alter the original discovery run/i)).toBeInTheDocument();
    fireEvent.change(screen.getByLabelText("Message Highland about this artifact"), {
      target: { value: "Add a next step." },
    });
    fireEvent.click(screen.getByRole("button", { name: "Send" }));

    expect(await screen.findByText("Read the Markdown file")).toBeInTheDocument();
    expect(screen.getByText("Read saved evidence")).toBeInTheDocument();
    expect(screen.getByText("Write a new revision")).toBeInTheDocument();
    expect(screen.getByText("Unsaved")).toBeInTheDocument();
    expect(onApply).not.toHaveBeenCalled();

    fireEvent.change(screen.getByLabelText("Ask for another change"), {
      target: { value: "Make that next step shorter." },
    });
    fireEvent.click(screen.getByRole("button", { name: "Send" }));

    expect(await screen.findByText("Made our working draft more concise.")).toBeInTheDocument();
    expect(screen.getByText("Read the working draft")).toBeInTheDocument();
    const secondRequest = JSON.parse(fetch.mock.calls[1][1].body as string);
    expect(secondRequest.draft_content).toBe(firstDraft);
    expect(secondRequest.history).toEqual([
      { role: "user", content: "Add a next step." },
      { role: "assistant", content: "Added a clearer next step." },
    ]);

    fireEvent.click(screen.getByRole("button", { name: "Apply and save revision 2" }));
    await waitFor(() => expect(onApply).toHaveBeenCalledWith(
      refinedDraft,
      artifact.citations,
      expect.stringContaining("AI artifact assistant"),
    ));
    expect(await screen.findByText(/wrote revision 2/i)).toBeInTheDocument();
  });

  it("does not edit an unsaved manual draft", () => {
    render(<ArtifactAssistantPanel artifact={artifact} onApply={vi.fn()} saveState="unsaved" />);
    fireEvent.change(screen.getByLabelText("Message Highland about this artifact"), {
      target: { value: "Change this" },
    });
    expect(screen.getByRole("button", { name: "Send" })).toBeDisabled();
    expect(screen.getByText(/Save your manual draft/i)).toBeInTheDocument();
  });
});
