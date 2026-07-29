import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { TraceTimeline } from "./discover-workspace";

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
