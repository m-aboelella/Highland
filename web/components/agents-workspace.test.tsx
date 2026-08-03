import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { AgentsWorkspace } from "./agents-workspace";

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

describe("AgentsWorkspace", () => {
  it("shows the real active profile and explains the read-only boundary", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue({
      ok: true,
      json: () => Promise.resolve([{
        id: "general",
        instructions: "Verify mutable facts before answering.",
        allowed_tools: ["*"],
        retrieval_defaults: { top_k: 8 },
        budgets: {
          max_steps: 20,
          max_model_calls: 10,
          max_wall_seconds: 120,
          max_tool_result_chars: 20000,
          max_context_chars: 100000,
        },
        models: {
          chat: "command-a-plus-05-2026",
          embedding: "embed-v4.0",
          rerank: "rerank-v4.0-fast",
        },
      }]),
    }));
    render(<AgentsWorkspace />);

    expect(await screen.findByText("General workspace agent")).toBeInTheDocument();
    expect(screen.getByText("command-a-plus-05-2026")).toBeInTheDocument();
    expect(screen.getByText(/Creating, editing, and switching profiles is not available/)).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Start a run with this agent" })).toHaveAttribute("href", "/");
    expect(screen.queryByRole("button", { name: /create agent/i })).not.toBeInTheDocument();
  });
});
