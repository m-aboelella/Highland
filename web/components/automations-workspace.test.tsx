import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, expect, test, vi } from "vitest";

import { AutomationsWorkspace } from "./automations-workspace";

beforeEach(() => {
  vi.stubGlobal("fetch", vi.fn()
    .mockResolvedValueOnce({ ok: true, json: async () => [] })
    .mockResolvedValueOnce({
      ok: true,
      json: async () => ({
        workflow: {
          id: "wf_health",
          name: "Weekly health",
          description: "",
          nodes: [
            { id: "start", kind: "trigger", name: "Start" },
            { id: "health", kind: "generate", name: "Classify health" },
          ],
          edges: [{ source: "start", target: "health" }],
        },
        planner: { rationale: "Review every active enterprise account." },
      }),
    }));
});

test("draft plan is explicitly reviewable before test or publication", async () => {
  render(<AutomationsWorkspace />);
  fireEvent.change(screen.getByLabelText("Natural-language goal"), {
    target: { value: "Review customer health" },
  });
  fireEvent.click(screen.getByRole("button", { name: "Draft plan" }));

  await waitFor(() => expect(screen.getByText("Weekly health")).toBeInTheDocument());
  expect(screen.getByLabelText("Workflow nodes")).toBeInTheDocument();
  expect(screen.getByRole("button", { name: "Test run" })).toBeInTheDocument();
  expect(screen.getByRole("button", { name: "Publish" })).toBeInTheDocument();
  expect(screen.getByText(/Review the model rationale/)).toBeInTheDocument();
});
