import { act, cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
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
    expect(screen.getByText("Model decision step 1")).toBeInTheDocument();
    expect(screen.getByText(/Requested Atlas CRM: get customer/)).toBeInTheDocument();
    expect(screen.getByText("Requested 1 tool")).toBeInTheDocument();
    expect(screen.getByText("Checked Atlas CRM: get customer")).toBeInTheDocument();
    expect(screen.getByText("Error handled")).toBeInTheDocument();
    expect(screen.getAllByText("Technical details")).toHaveLength(2);
    expect(screen.getAllByText(/crm__get_customer/)).toHaveLength(2);
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
    expect(screen.getByText("mock://archive/doc_runbook")).toBeInTheDocument();
    expect(screen.queryByRole("link", { name: /canonical source/i })).not.toBeInTheDocument();
    expect(screen.getByText(/not a public website/i)).toBeInTheDocument();
  });

  it("explains tool-backed citation evidence and opens its record in Highland", () => {
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
                  source_url: "https://atlas.summit.test/customers/cus_northwind",
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
    expect(screen.getByText("https://atlas.summit.test/customers/cus_northwind")).toBeInTheDocument();
    expect(screen.getByText("View source record")).toBeInTheDocument();
    expect(screen.getByText(/not a public website/i)).toBeInTheDocument();
    expect(screen.queryByRole("link", { name: /open external source/i })).not.toBeInTheDocument();
  });

  it("keeps a genuinely routable source as an external link", () => {
    render(
      <EvidencePanel
        evidence={[{
          id: "chk_real",
          source_id: "doc_real",
          title: "External policy",
          text: "Policy text.",
          source_system: "archive",
          source_type: "policy",
          source_url: "https://docs.example.com/policy",
          updated_at: "2026-07-29T00:00:00Z",
          location: { section: "Policy" },
        }]}
        refreshed={false}
      />,
    );
    expect(screen.getByRole("link", { name: /Open external source/ })).toHaveAttribute(
      "href",
      "https://docs.example.com/policy",
    );
  });

  it("shows the complete response when a citation does not identify one list item", () => {
    render(
      <CitationInspector
        citation={{
          start: 0,
          end: 19,
          text: "Two accounts changed",
          source_ids: [],
          tool_call_ids: ["call-list"],
        }}
        citationNumber={2}
        evidence={[]}
        events={[
          {
            id: 1,
            type: "tool_call",
            timestamp: "2026-07-29T00:00:00Z",
            payload: { tool_call: { id: "call-list", name: "crm__list_customers", arguments: {} } },
          },
          {
            id: 2,
            type: "tool_result",
            timestamp: "2026-07-29T00:00:01Z",
            payload: {
              tool_call_id: "call-list",
              content: JSON.stringify({ items: [{ id: "one" }, { id: "two" }] }),
            },
          },
        ]}
      />,
    );
    expect(screen.getByText("Tool response supporting this claim")).toBeInTheDocument();
    expect(screen.getByText("View supporting tool response")).toBeInTheDocument();
    expect(screen.getByText(/returned 2 records/i)).toBeInTheDocument();
    const response = screen.getByText(/"items": \[/).closest("pre");
    expect(response).toHaveClass("tool-response-json");
    expect(response?.closest("details")).toHaveClass("tool-response");
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

  it("creates an artifact from a restored discovery and opens the editor", async () => {
    const trace = [{
      id: 1,
      type: "final",
      timestamp: "2026-08-03T22:38:57Z",
      payload: { content: "Northwind needs a capacity review." },
    }];
    const fetchMock = vi.fn((input: string | URL | Request, init?: RequestInit) => {
      const url = String(input);
      if (url.endsWith("/runs") && !init?.method) {
        return Promise.resolve({
          ok: true,
          json: () => Promise.resolve([{
            run_id: "run-artifact",
            status: "completed",
            event_count: 1,
            last_event_id: 1,
            conversation_id: "con-artifact",
            prompt: "Prepare the Northwind meeting",
          }]),
        });
      }
      if (url.endsWith("/runs/run-artifact/summary")) {
        return Promise.resolve({
          ok: true,
          json: () => Promise.resolve({
            run_id: "run-artifact",
            status: "completed",
            final: { content: "Northwind needs a capacity review." },
          }),
        });
      }
      if (url.endsWith("/runs/run-artifact/trace")) {
        return Promise.resolve({ ok: true, json: () => Promise.resolve(trace) });
      }
      if (url.endsWith("/conversations/con-artifact")) {
        return Promise.resolve({
          ok: true,
          json: () => Promise.resolve({
            messages: [{ id: "msg-answer", role: "assistant", run_id: "run-artifact" }],
          }),
        });
      }
      if (url.endsWith("/artifacts/generate") && init?.method === "POST") {
        return Promise.resolve({
          ok: true,
          json: () => Promise.resolve({
            id: "art-1",
            title: "Northwind briefing",
            artifact_type: "briefing",
            content: "## Capacity\n\nReview capacity.",
            citations: [],
            revision: 1,
            conversation_id: "con-artifact",
            run_id: "run-artifact",
            message_id: "msg-answer",
            updated_at: "2026-08-03T22:39:00Z",
          }),
        });
      }
      return Promise.resolve({ ok: false });
    });
    vi.stubGlobal("fetch", fetchMock);
    render(<DiscoverWorkspace />);

    fireEvent.click(await screen.findByRole("button", { name: /Prepare the Northwind meeting/ }));
    fireEvent.click(await screen.findByRole("button", { name: "Turn into artifact" }));

    expect(await screen.findByRole("region", { name: "Artifact editor" })).toBeInTheDocument();
    expect(screen.getByRole("heading", { name: "Northwind briefing" })).toBeInTheDocument();
    const generationCall = fetchMock.mock.calls.find(([input, init]) => (
      String(input).endsWith("/artifacts/generate") && init?.method === "POST"
    ));
    expect(JSON.parse(String(generationCall?.[1]?.body))).toEqual({
      artifact_type: "briefing",
      conversation_id: "con-artifact",
      message_id: "msg-answer",
    });
  });

  it("shows artifact generation errors beside the action", async () => {
    const trace = [{
      id: 1,
      type: "final",
      timestamp: "2026-08-03T22:38:57Z",
      payload: { content: "Northwind needs a capacity review." },
    }];
    vi.stubGlobal("fetch", vi.fn((input: string | URL | Request, init?: RequestInit) => {
      const url = String(input);
      if (url.endsWith("/runs") && !init?.method) {
        return Promise.resolve({
          ok: true,
          json: () => Promise.resolve([{
            run_id: "run-artifact",
            status: "completed",
            event_count: 1,
            last_event_id: 1,
            conversation_id: "con-artifact",
            prompt: "Prepare the Northwind meeting",
          }]),
        });
      }
      if (url.endsWith("/runs/run-artifact/summary")) {
        return Promise.resolve({
          ok: true,
          json: () => Promise.resolve({ status: "completed", final: trace[0].payload }),
        });
      }
      if (url.endsWith("/runs/run-artifact/trace")) {
        return Promise.resolve({ ok: true, json: () => Promise.resolve(trace) });
      }
      if (url.endsWith("/conversations/con-artifact")) {
        return Promise.resolve({
          ok: true,
          json: () => Promise.resolve({
            messages: [{ id: "msg-answer", role: "assistant", run_id: "run-artifact" }],
          }),
        });
      }
      if (url.endsWith("/artifacts/generate") && init?.method === "POST") {
        return Promise.resolve({
          ok: false,
          json: () => Promise.resolve({ detail: "Artifact generation model is unavailable." }),
        });
      }
      return Promise.resolve({ ok: false });
    }));
    render(<DiscoverWorkspace />);

    fireEvent.click(await screen.findByRole("button", { name: /Prepare the Northwind meeting/ }));
    const create = await screen.findByRole("button", { name: "Turn into artifact" });
    fireEvent.click(create);

    const alert = await screen.findByRole("alert");
    expect(alert).toHaveTextContent("Artifact generation model is unavailable.");
    expect(create.parentElement).toContainElement(alert);
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
    expect(screen.getByText(/Rejoining the saved run/)).toBeInTheDocument();
    expect(screen.queryByText(/persisted trace can still be replayed/i)).not.toBeInTheDocument();
    act(() => FakeEventSource.instance?.onopen?.());
    expect(screen.queryByText(/Rejoining the saved run/)).not.toBeInTheDocument();

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

  it("creates a fresh model conversation for every discovery", async () => {
    class FakeEventSource {
      static instances: FakeEventSource[] = [];
      listeners = new Map<string, (message: MessageEvent) => void>();
      onopen: (() => void) | null = null;
      onerror: (() => void) | null = null;

      constructor(_url: string) {
        FakeEventSource.instances.push(this);
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
    let conversationNumber = 0;
    const requestedRunUrls: string[] = [];
    const conversationBodies: string[] = [];
    vi.stubGlobal("fetch", vi.fn((input: string | URL | Request, init?: RequestInit) => {
      const url = String(input);
      if (url.endsWith("/runs") && !init?.method) {
        return Promise.resolve({ ok: true, json: () => Promise.resolve([]) });
      }
      if (url.endsWith("/conversations") && init?.method === "POST") {
        conversationNumber += 1;
        conversationBodies.push(String(init.body));
        return Promise.resolve({
          ok: true,
          json: () => Promise.resolve({ id: `con-${conversationNumber}` }),
        });
      }
      if (url.includes("/conversations/con-") && url.endsWith("/runs")) {
        requestedRunUrls.push(url);
        return Promise.resolve({
          ok: true,
          json: () => Promise.resolve({ run_id: `run-${requestedRunUrls.length}` }),
        });
      }
      return Promise.resolve({ ok: false });
    }));
    render(<DiscoverWorkspace />);

    const question = screen.getByLabelText("Ask Highland");
    fireEvent.change(question, { target: { value: "First discovery" } });
    fireEvent.click(screen.getByRole("button", { name: "Start discovery" }));
    await waitFor(() => expect(FakeEventSource.instances).toHaveLength(1));
    act(() => FakeEventSource.instances[0].emit("final", {
      id: 1,
      type: "final",
      timestamp: "2026-08-03T22:38:58Z",
      payload: { content: "First answer" },
    }));

    fireEvent.change(question, { target: { value: "Second discovery" } });
    fireEvent.click(screen.getByRole("button", { name: "Start discovery" }));
    await waitFor(() => expect(FakeEventSource.instances).toHaveLength(2));

    expect(conversationNumber).toBe(2);
    expect(conversationBodies.map((body) => JSON.parse(body))).toEqual([
      { title: "First discovery" },
      { title: "Second discovery" },
    ]);
    expect(requestedRunUrls).toEqual([
      "http://127.0.0.1:8080/conversations/con-1/runs",
      "http://127.0.0.1:8080/conversations/con-2/runs",
    ]);
  });

  it("puts a live run before history and explains a terminal run failure", async () => {
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
        return Promise.resolve({ ok: true, json: () => Promise.resolve({ run_id: "run-failed" }) });
      }
      return Promise.resolve({ ok: false });
    }));
    render(<DiscoverWorkspace />);
    fireEvent.change(screen.getByLabelText("Ask Highland"), {
      target: { value: "Prepare the meeting" },
    });
    fireEvent.click(screen.getByRole("button", { name: "Start discovery" }));

    await waitFor(() => expect(FakeEventSource.instance).toBeDefined());
    const outputHeading = screen.getByRole("heading", { name: "Discovery in progress" });
    const historyHeading = screen.getByRole("heading", { name: "Previous runs" });
    expect(outputHeading.compareDocumentPosition(historyHeading)).toBe(
      Node.DOCUMENT_POSITION_FOLLOWING,
    );
    expect(screen.getByText("Highland is investigating your question")).toBeInTheDocument();

    act(() => {
      FakeEventSource.instance?.emit("model_call", {
        id: 1,
        type: "model_call",
        timestamp: "2026-08-03T22:38:57Z",
        payload: { step: 10, finish_reason: "tool_call" },
      });
      FakeEventSource.instance?.emit("tool_call", {
        id: 2,
        type: "tool_call",
        timestamp: "2026-08-03T22:38:58Z",
        payload: {
          tool_call: {
            id: "metric-call",
            name: "observability__query_deployment_metrics",
            arguments: { customer_id: "cus_northwind", metric: "retrieval_latency_p95" },
          },
        },
      });
      FakeEventSource.instance?.emit("tool_result", {
        id: 3,
        type: "tool_result",
        timestamp: "2026-08-03T22:38:59Z",
        payload: {
          tool_call_id: "metric-call",
          is_error: true,
          content: "Beacon returned HTTP 404: Metric not found for customer",
        },
      });
      FakeEventSource.instance?.emit("run_failed", {
        id: 4,
        type: "run_failed",
        timestamp: "2026-08-03T22:39:00Z",
        payload: { reason: "maximum steps or runtime budget" },
      });
    });

    expect(screen.getByRole("heading", { name: "Discovery stopped" })).toBeInTheDocument();
    const failure = screen.getByRole("alert");
    expect(within(failure).getByText(/reached its run limit before writing the answer/i)).toBeInTheDocument();
    expect(within(failure).getByText(/used all 1 available model calls/i)).toBeInTheDocument();
    expect(within(failure).getByText(/Beacon: query deployment metrics/)).toHaveTextContent(
      "Metric not found for customer",
    );
    expect(screen.getByRole("button", { name: "Start discovery" })).toBeEnabled();

    act(() => FakeEventSource.instance?.onerror?.());
    expect(screen.queryByText(/Rejoining the saved run/)).not.toBeInTheDocument();
  });
});
