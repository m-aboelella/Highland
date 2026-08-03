import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import {
  CitationInspector,
  CitedAnswer,
  DiscoverWorkspace,
  EvidencePanel,
  TraceTimeline,
} from "./discover-workspace";

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

describe("TraceTimeline", () => {
  it("renders concise and expandable event details", () => {
    render(
      <TraceTimeline
        events={[
          {
            id: 1,
            type: "model_call",
            timestamp: "2026-07-29T00:00:00Z",
            payload: { step: 1, finish_reason: "tool_call" },
          },
          {
            id: 2,
            type: "tool_call",
            timestamp: "2026-07-29T00:00:01Z",
            payload: {
              tool_call: {
                id: "call-1",
                name: "crm__get_customer",
                arguments: { customer_id: "northwind" },
              },
            },
          },
          {
            id: 3,
            type: "tool_result",
            timestamp: "2026-07-29T00:00:02Z",
            payload: { tool_call_id: "call-1", content: "Not found", is_error: true },
          },
        ]}
      />,
    );
    expect(screen.getByText("How Highland reached this answer")).toBeInTheDocument();
    expect(screen.getByText("Model reasoning step 1")).toBeInTheDocument();
    expect(screen.getByText("Checked Atlas CRM: get customer")).toBeInTheDocument();
    expect(screen.getByText("Error handled")).toBeInTheDocument();
    expect(screen.getAllByText("Technical details")).toHaveLength(2);
    expect(screen.getByText(/crm__get_customer/)).toBeInTheDocument();
  });
});

describe("answer evidence", () => {
  it("renders Markdown and adds an actionable citation control", () => {
    const select = vi.fn();
    render(
      <CitedAnswer
        answer={"## Finding\n\nObserved latency; cause uncertain."}
        citations={[{
          start: 12,
          end: 28,
          text: "Observed latency",
          source_ids: ["chk_1"],
        }]}
        onSelect={select}
      />,
    );
    expect(screen.getByRole("heading", { level: 2, name: "Finding" })).toBeInTheDocument();
    const marker = screen.getByRole("button", { name: "View evidence for citation 1" });
    fireEvent.click(marker);
    expect(select).toHaveBeenCalledWith(0);
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

  it("explains tool-backed citation evidence and links its canonical record", () => {
    render(
      <CitationInspector
        citation={{
          start: 0,
          end: 15,
          text: "Northwind Bank",
          source_ids: [],
          tool_call_ids: ["call-1"],
        }}
        citationNumber={1}
        evidence={[]}
        events={[
          {
            id: 1,
            type: "tool_call",
            timestamp: "2026-07-29T00:00:00Z",
            payload: {
              tool_call: {
                id: "call-1",
                name: "crm__list_customers",
                arguments: { status: "active" },
              },
            },
          },
          {
            id: 2,
            type: "tool_result",
            timestamp: "2026-07-29T00:00:01Z",
            payload: {
              tool_call_id: "call-1",
              is_error: false,
              content: JSON.stringify({
                items: [{
                  id: "cus_northwind",
                  name: "Northwind Bank",
                  source_url: "https://atlas.test/customers/cus_northwind",
                }],
              }),
            },
          },
        ]}
      />,
    );

    expect(screen.getByText("Citation 1 · Live tool evidence")).toBeInTheDocument();
    expect(screen.getByText("Atlas CRM")).toBeInTheDocument();
    expect(screen.getByText("list customers")).toBeInTheDocument();
    expect(screen.getByText("Northwind Bank", { selector: "blockquote" })).toBeInTheDocument();
    expect(screen.getByRole("link", { name: /Open canonical source/ })).toHaveAttribute(
      "href",
      "https://atlas.test/customers/cus_northwind",
    );
  });
});

describe("DiscoverWorkspace", () => {
  it("shows an actionable API error and leaves the form reusable", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue({ ok: false }));
    render(<DiscoverWorkspace />);

    fireEvent.change(screen.getByLabelText("Ask Highland"), {
      target: { value: "Prepare a customer briefing" },
    });
    fireEvent.click(screen.getByRole("button", { name: "Start discovery" }));

    expect(await screen.findByRole("alert")).toHaveTextContent(
      "A conversation could not be created.",
    );
    expect(screen.getByRole("button", { name: "Start discovery" })).toBeEnabled();
  });

  it("lists previous runs and restores a persisted output and trace", async () => {
    const trace = [
      {
        id: 1,
        type: "run_started",
        timestamp: "2026-08-02T22:38:42Z",
        payload: { conversation_id: "con-1" },
      },
      {
        id: 2,
        type: "model_delta",
        timestamp: "2026-08-02T22:38:57Z",
        payload: { text: "Northwind is preparing for its renewal." },
      },
      {
        id: 3,
        type: "final",
        timestamp: "2026-08-02T22:38:57Z",
        payload: { content: "Northwind is preparing for its renewal." },
      },
    ];
    vi.stubGlobal("fetch", vi.fn((input: string | URL | Request) => {
      const url = String(input);
      if (url.endsWith("/runs")) {
        return Promise.resolve({
          ok: true,
          json: () => Promise.resolve([
            {
              run_id: "run-1",
              status: "completed",
              event_count: 3,
              last_event_id: 3,
              conversation_id: "con-1",
              prompt: "Prepare me for the Northwind meeting",
              updated_at: "2026-08-02T22:38:57Z",
              final_preview: "Northwind is preparing for its renewal.",
            },
          ]),
        });
      }
      if (url.endsWith("/runs/run-1/summary")) {
        return Promise.resolve({
          ok: true,
          json: () => Promise.resolve({
            run_id: "run-1",
            status: "completed",
            event_count: 3,
            last_event_id: 3,
            final: { content: "Northwind is preparing for its renewal." },
          }),
        });
      }
      if (url.endsWith("/runs/run-1/trace")) {
        return Promise.resolve({ ok: true, json: () => Promise.resolve(trace) });
      }
      return Promise.resolve({ ok: false });
    }));
    render(<DiscoverWorkspace />);

    const previousRun = await screen.findByRole("button", {
      name: /Prepare me for the Northwind meeting/,
    });
    fireEvent.click(previousRun);

    expect(await screen.findByText("Northwind is preparing for its renewal.")).toBeInTheDocument();
    expect(screen.getByText("Completed the grounded answer")).toBeInTheDocument();
    expect(previousRun).toHaveAttribute("aria-expanded", "true");
    fireEvent.click(previousRun);
    expect(screen.queryByText("Grounded answer and evidence")).not.toBeInTheDocument();
    expect(previousRun).toHaveAttribute("aria-expanded", "false");
  });

  it("reconnects an interrupted live stream and renders its final output", async () => {
    class FakeEventSource {
      static instance?: FakeEventSource;
      listeners = new Map<string, (message: MessageEvent) => void>();
      onopen: (() => void) | null = null;
      onerror: (() => void) | null = null;

      constructor(_url: string) {
        FakeEventSource.instance = this;
      }

      addEventListener(name: string, listener: (message: MessageEvent) => void) {
        this.listeners.set(name, listener);
      }

      emit(name: string, event: object) {
        this.listeners.get(name)?.(new MessageEvent(name, { data: JSON.stringify(event) }));
      }

      close() {}
    }
    vi.stubGlobal("EventSource", FakeEventSource);
    vi.stubGlobal("fetch", vi.fn((input: string | URL | Request, init?: RequestInit) => {
      const url = String(input);
      if (url.endsWith("/runs") && !init?.method) {
        return Promise.resolve({ ok: true, json: () => Promise.resolve([]) });
      }
      if (url.endsWith("/conversations") && init?.method === "POST") {
        return Promise.resolve({ ok: true, json: () => Promise.resolve({ id: "con-1" }) });
      }
      if (url.endsWith("/conversations/con-1/runs") && init?.method === "POST") {
        return Promise.resolve({ ok: true, json: () => Promise.resolve({ run_id: "run-live" }) });
      }
      return Promise.resolve({ ok: false });
    }));
    render(<DiscoverWorkspace />);
    fireEvent.change(screen.getByLabelText("Ask Highland"), {
      target: { value: "Prepare the meeting" },
    });
    fireEvent.click(screen.getByRole("button", { name: "Start discovery" }));
    await waitFor(() => expect(FakeEventSource.instance).toBeDefined());

    act(() => FakeEventSource.instance?.onerror?.());
    expect(screen.getByRole("status")).toHaveTextContent("Reconnecting");
    expect(screen.queryByText(/persisted trace can still be replayed/i)).not.toBeInTheDocument();
    act(() => FakeEventSource.instance?.onopen?.());
    expect(screen.queryByRole("status")).not.toBeInTheDocument();

    act(() => {
      FakeEventSource.instance?.emit("model_delta", {
        id: 1,
        type: "model_delta",
        timestamp: "2026-08-02T22:38:57Z",
        payload: { text: "Meeting response" },
      });
      FakeEventSource.instance?.emit("final", {
        id: 2,
        type: "final",
        timestamp: "2026-08-02T22:38:58Z",
        payload: { content: "Meeting response" },
      });
    });

    expect(screen.getByText("Meeting response")).toBeInTheDocument();
    expect(screen.getByText("Completed the grounded answer")).toBeInTheDocument();
  });
});
