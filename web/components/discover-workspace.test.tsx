import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { CitedAnswer, EvidencePanel, TraceTimeline } from "./discover-workspace";

describe("TraceTimeline", () => {
  it("renders concise and expandable event details", () => {
    render(
      <TraceTimeline
        events={[
          {
            id: 1,
            type: "tool_result",
            timestamp: "2026-07-29T00:00:00Z",
            payload: { tool: "crm__get_customer", duration_ms: 12 },
          },
        ]}
      />,
    );
    expect(screen.getByText("tool result")).toBeInTheDocument();
    expect(screen.getByText("Inspect event")).toBeInTheDocument();
    expect(screen.getByText(/crm__get_customer/)).toBeInTheDocument();
  });
});

describe("answer evidence", () => {
  it("links cited claims and distinguishes unsupported text", () => {
    render(
      <CitedAnswer
        answer="Observed latency; cause uncertain."
        citations={[{ start: 0, end: 16, source_ids: ["chk_1"] }]}
        onSelect={() => undefined}
      />,
    );
    expect(screen.getByText("Observed latency")).toHaveClass("supported");
    expect(screen.getByText("; cause uncertain.")).toHaveClass("unsupported");
    expect(screen.getByRole("button", { name: "1" })).toBeInTheDocument();
  });

  it("shows the exact selected passage and canonical metadata", () => {
    render(
      <EvidencePanel
        evidence={[
          {
            id: "chk_1",
            source_id: "doc_runbook",
            title: "Latency runbook",
            text: "Exact compaction passage.",
            source_system: "archive",
            source_type: "runbook",
            source_url: "mock://archive/doc_runbook",
            customer_id: "cus_northwind",
            updated_at: "2026-07-29T00:00:00Z",
            location: { section: "Compaction" },
          },
        ]}
        selectedId="chk_1"
        refreshed
      />,
    );
    expect(screen.getByText("Exact compaction passage.")).toBeInTheDocument();
    expect(screen.getByText("Mutable fact refreshed through MCP")).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Open canonical source" })).toHaveAttribute(
      "href",
      "mock://archive/doc_runbook",
    );
  });
});
