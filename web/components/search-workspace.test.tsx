import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { SearchWorkspace } from "./search-workspace";

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

describe("SearchWorkspace", () => {
  it("searches indexed evidence without starting an agent run", async () => {
    const fetch = vi.fn().mockResolvedValue({
      ok: true,
      json: () => Promise.resolve({
        query: "latency",
        timings: { total_ms: 12.4 },
        results: [{
          score: 0.923,
          chunk: {
            id: "chk-1",
            source_system: "archive",
            source_id: "doc-1",
            title: "Latency runbook",
            text: "## Mitigation\n\nPause compaction.",
            source_type: "runbook",
            source_url: "https://archive.summit.test/docs/doc-1",
            updated_at: "2026-07-29T00:00:00Z",
            location: { section: "Mitigation" },
          },
        }],
      }),
    });
    vi.stubGlobal("fetch", fetch);
    render(<SearchWorkspace />);

    expect(screen.getByText(/does not start a conversation/i)).toBeInTheDocument();
    fireEvent.change(screen.getByLabelText("Search indexed knowledge"), {
      target: { value: "latency" },
    });
    fireEvent.click(screen.getByRole("button", { name: "Search sources" }));

    expect(await screen.findByText("Latency runbook")).toBeInTheDocument();
    expect(screen.getByRole("heading", { name: "Mitigation" })).toBeInTheDocument();
    expect(screen.queryByText(/agent loop/i)).not.toBeInTheDocument();
    expect(fetch).toHaveBeenCalledWith(
      expect.stringMatching(/\/discover\/search$/),
      expect.objectContaining({ method: "POST" }),
    );
    const request = JSON.parse(String(fetch.mock.calls[0][1]?.body));
    expect(request.query).toBe("latency");
  });

  it("renders a useful empty state", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue({
      ok: true,
      json: () => Promise.resolve({ query: "missing", timings: { total_ms: 2 }, results: [] }),
    }));
    render(<SearchWorkspace />);
    fireEvent.change(screen.getByLabelText("Search indexed knowledge"), {
      target: { value: "missing" },
    });
    fireEvent.click(screen.getByRole("button", { name: "Search sources" }));
    await waitFor(() => expect(screen.getByText("No matching passages.")).toBeInTheDocument());
  });
});
