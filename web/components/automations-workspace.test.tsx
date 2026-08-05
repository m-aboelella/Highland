import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, beforeEach, expect, test, vi } from "vitest";

import { AutomationsWorkspace } from "./automations-workspace";

afterEach(cleanup);

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

test("planning failures are shown as readable alerts", async () => {
  vi.stubGlobal("fetch", vi.fn()
    .mockResolvedValueOnce({ ok: true, json: async () => [] })
    .mockResolvedValueOnce({
      ok: false,
      json: async () => ({
        detail: (
          "Couldn't create the workflow because the model returned no workflow plan. "
          + "Please try again."
        ),
      }),
    }));

  render(<AutomationsWorkspace />);
  fireEvent.change(screen.getByLabelText("Natural-language goal"), {
    target: { value: "Every morning, show me the latest health metrics for northwind" },
  });
  fireEvent.click(screen.getByRole("button", { name: "Draft plan" }));

  const alert = await screen.findByRole("alert");
  expect(alert).toHaveTextContent("model returned no workflow plan");
  expect(alert).not.toHaveTextContent("ProposedWorkflow");
  expect(alert).not.toHaveTextContent("pydantic");
});

test("test run saves and executes the current in-browser draft in one request", async () => {
  vi.mocked(fetch).mockResolvedValueOnce({
    ok: true,
    json: async () => ({
      id: "wrun_health",
      workflow_id: "wf_health",
      workflow_version: 0,
      status: "completed",
      nodes: {},
    }),
  } as Response);

  render(<AutomationsWorkspace />);
  fireEvent.change(screen.getByLabelText("Natural-language goal"), {
    target: { value: "Review customer health" },
  });
  fireEvent.click(screen.getByRole("button", { name: "Draft plan" }));
  await screen.findByText("Weekly health");
  fireEvent.click(screen.getByRole("button", { name: "Test run" }));

  await screen.findByText(/Test run completed/);
  const request = vi.mocked(fetch).mock.calls[2];
  expect(request[0]).toBe("http://127.0.0.1:8080/workflows/wf_health/runs");
  expect(JSON.parse(String(request[1]?.body))).toMatchObject({
    test: true,
    workflow: { id: "wf_health", name: "Weekly health" },
  });
});

test("run history presents the customer outcome before technical JSON", async () => {
  vi.stubGlobal("fetch", vi.fn().mockResolvedValueOnce({
    ok: true,
    json: async () => ([{
      id: "wrun_northwind",
      workflow_id: "wf_daily_northwind_metrics",
      workflow_version: 0,
      status: "completed",
      model_calls: 1,
      tool_calls: 3,
      started_at: "2026-08-05T08:00:00.000Z",
      updated_at: "2026-08-05T08:00:02.400Z",
      nodes: {
        start: { node_id: "start", status: "completed", output: {} },
        query_p95: {
          node_id: "query_p95",
          status: "completed",
          duration_ms: 40,
          output: { latest: 1300, unit: "ms" },
        },
        query_errors: {
          node_id: "query_errors",
          status: "completed",
          duration_ms: 35,
          output: { latest: 0.005, unit: "ratio" },
        },
        generate_review: {
          node_id: "generate_review",
          status: "completed",
          duration_ms: 2200,
          output: (
            "## Northwind morning health\n\n"
            + "Latency is elevated, while traffic remains stable. **Monitor shard 3 today.**"
          ),
        },
      },
    }]),
  }));

  render(<AutomationsWorkspace />);
  fireEvent.click(await screen.findByRole("button", { name: "View result" }));

  expect(screen.getByText("The workflow produced a useful customer update.")).toBeInTheDocument();
  expect(screen.getByRole("heading", { name: "Northwind morning health" })).toBeInTheDocument();
  expect(screen.getByText(/Monitor shard 3 today/)).toBeInTheDocument();
  const summary = screen.getByLabelText("Test run summary");
  expect(within(summary).getByText("3", { selector: "dd" })).toBeInTheDocument();
  expect(within(summary).getByText("1", { selector: "dd" })).toBeInTheDocument();
  expect(within(summary).getByText("2.4 s", { selector: "dd" })).toBeInTheDocument();
  expect(screen.getByText("Safe draft · nothing published")).toBeInTheDocument();

  fireEvent.click(screen.getByText("Technical details"));
  expect(screen.getByText("Generate review")).toBeInTheDocument();
  expect(screen.getByRole("link", { name: "Open raw JSON trace" })).toHaveAttribute(
    "href",
    "http://127.0.0.1:8080/runs/wrun_northwind/trace"
  );
});

test("failed test runs stay visible and explain the failure", async () => {
  vi.mocked(fetch).mockResolvedValueOnce({
    ok: true,
    json: async () => ({
      id: "wrun_health",
      workflow_id: "wf_health",
      workflow_version: 0,
      status: "failed",
      error: "ToolRejected: Invalid arguments for observability__get_deployment",
      nodes: {},
    }),
  } as Response);

  render(<AutomationsWorkspace />);
  fireEvent.change(screen.getByLabelText("Natural-language goal"), {
    target: { value: "Review customer health" },
  });
  fireEvent.click(screen.getByRole("button", { name: "Draft plan" }));
  await screen.findByText("Weekly health");
  fireEvent.click(screen.getByRole("button", { name: "Test run" }));

  const alert = await screen.findByRole("alert");
  expect(alert).toHaveTextContent(
    "Test run failed: Invalid arguments for observability__get_deployment"
  );
  expect(screen.getByRole("link", { name: "Open raw JSON trace" })).toHaveAttribute(
    "href",
    "http://127.0.0.1:8080/runs/wrun_health/trace"
  );
});

test("invalid stale drafts get a short recovery message", async () => {
  vi.mocked(fetch).mockResolvedValueOnce({
    ok: false,
    json: async () => ({
      detail: [{ loc: ["body", "workflow", "id"], msg: "String should match pattern" }],
    }),
  } as Response);

  render(<AutomationsWorkspace />);
  fireEvent.change(screen.getByLabelText("Natural-language goal"), {
    target: { value: "Review customer health" },
  });
  fireEvent.click(screen.getByRole("button", { name: "Draft plan" }));
  await screen.findByText("Weekly health");
  fireEvent.click(screen.getByRole("button", { name: "Test run" }));

  expect(await screen.findByRole("alert")).toHaveTextContent(
    "This draft is invalid. Generate a new plan, then try the test run again."
  );
});
