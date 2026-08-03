"use client";

import { FormEvent, useCallback, useEffect, useRef, useState } from "react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";

import { ArtifactDocument, ArtifactEditor } from "./artifact-editor";
import { SourceFilterControls } from "./source-filter-controls";

export type TraceEvent = {
  id: number;
  type: string;
  timestamp: string;
  payload: Record<string, unknown>;
};

type RunSummary = {
  run_id: string;
  status: string;
  event_count: number;
  last_event_id: number;
  started_at?: string;
  updated_at?: string;
  conversation_id?: string;
  prompt?: string;
  final?: { content?: string };
  final_preview?: string;
};

type Citation = {
  start: number;
  end: number;
  text?: string;
  source_ids: string[];
  tool_call_ids?: string[];
};

type Evidence = {
  id: string;
  source_id: string;
  title: string;
  text: string;
  source_system: string;
  source_type: string;
  source_url: string;
  customer_id?: string;
  updated_at: string;
  location: { section: string };
};

const API = process.env.NEXT_PUBLIC_HIGHLAND_API_URL ?? "http://127.0.0.1:8080";

const SOURCE_LABELS: Record<string, string> = {
  crm: "Atlas CRM",
  knowledge: "Archive",
  support: "Relay Desk",
  observability: "Beacon",
  communications: "Pulse",
  projects: "Track",
};

const TERMINAL_EVENT_TYPES = [
  "final", "error", "run_cancelled", "run_completed", "run_failed",
];

function asRecord(value: unknown): Record<string, unknown> {
  return value && typeof value === "object" && !Array.isArray(value)
    ? value as Record<string, unknown>
    : {};
}

function toolDetails(event: TraceEvent) {
  const call = asRecord(event.payload.tool_call);
  return {
    id: String(call.id ?? ""),
    name: String(call.name ?? "tool"),
    arguments: asRecord(call.arguments),
  };
}

type ToolChoice = ReturnType<typeof toolDetails>;

function embeddedToolChoices(event: TraceEvent): ToolChoice[] | undefined {
  if (!("tool_calls" in event.payload)) return undefined;
  if (!Array.isArray(event.payload.tool_calls)) return [];
  return event.payload.tool_calls.map((item) => {
    const call = asRecord(item);
    return {
      id: String(call.id ?? ""),
      name: String(call.name ?? "tool"),
      arguments: asRecord(call.arguments),
    };
  });
}

function choicesAfterModel(events: TraceEvent[], modelIndex: number) {
  const embedded = embeddedToolChoices(events[modelIndex]);
  if (embedded !== undefined) return embedded;
  const choices: ToolChoice[] = [];
  for (const event of events.slice(modelIndex + 1)) {
    if (["model_call", "final", "error", "run_cancelled"].includes(event.type)) break;
    if (event.type === "tool_call") choices.push(toolDetails(event));
  }
  return choices;
}

function friendlyTool(toolName: string) {
  const [system, action = toolName] = toolName.split("__", 2);
  return {
    system: SOURCE_LABELS[system] ?? system.replaceAll("_", " "),
    action: action.replaceAll("_", " "),
  };
}

function formatArguments(arguments_: Record<string, unknown>) {
  const entries = Object.entries(arguments_);
  if (!entries.length) return "No filters";
  return entries.map(([name, value]) => `${name.replaceAll("_", " ")}: ${String(value)}`).join(" · ");
}

function cleanToolError(content: unknown) {
  return String(content ?? "The tool returned an error.")
    .replace(/^Error executing tool [^:]+:\s*/i, "")
    .replace(/^\w+ returned HTTP \d+:\s*/i, "");
}

function runFailureInfo(events: TraceEvent[]) {
  const terminal = [...events].reverse().find((event) => (
    event.type === "run_failed" || event.type === "error"
  ));
  if (!terminal) return undefined;
  const modelCalls = events.filter((event) => event.type === "model_call").length;
  const failedResult = [...events].reverse().find((event) => (
    event.type === "tool_result" && Boolean(event.payload.is_error)
  ));
  const failedCall = failedResult
    ? events.find((event) => (
        event.type === "tool_call"
        && toolDetails(event).id === String(failedResult.payload.tool_call_id ?? "")
      ))
    : undefined;
  const failedTool = failedCall ? friendlyTool(toolDetails(failedCall).name) : undefined;
  const reason = String(terminal.payload.reason ?? terminal.payload.message ?? "The run stopped.");
  const budgetReached = reason.includes("maximum steps") || reason.includes("runtime budget");
  return {
    title: budgetReached
      ? "Highland reached its run limit before writing the answer"
      : "Discovery stopped before writing the answer",
    message: budgetReached
      ? `The agent used all ${modelCalls} available model calls while collecting and verifying evidence.`
      : reason,
    detail: failedResult
      ? `${failedTool ? `${failedTool.system}: ${failedTool.action}` : "The last tool check"} failed with “${cleanToolError(failedResult.payload.content)}”. ${budgetReached ? "Highland attempted to recover, but the run limit was reached before it could synthesize a final response." : "Open the agent loop below to see how Highland handled the failed check."}`
      : "Open the agent loop below to see the last completed step.",
    reason,
  };
}

export function TraceTimeline({ events }: { events: TraceEvent[] }) {
  const toolResults = new Map(
    events
      .filter((event) => event.type === "tool_result")
      .map((event) => [String(event.payload.tool_call_id ?? ""), event]),
  );
  const citationCount = events.filter((event) => event.type === "citation").length;
  const modelCount = events.filter((event) => event.type === "model_call").length;
  const toolCount = events.filter((event) => event.type === "tool_call").length;
  const failure = runFailureInfo(events);
  const steps = events.flatMap((event, eventIndex) => {
    if (event.type === "retrieval") {
      const results = Array.isArray(event.payload.results) ? event.payload.results.length : 0;
      return [{
        event,
        kind: "retrieval",
        title: "Searched indexed knowledge",
        description: `Retrieved ${results} relevant passages before the agent loop began.`,
        status: "Context",
        details: event.payload,
      }];
    }
    if (event.type === "model_call") {
      const step = String(event.payload.step ?? modelCount);
      const choices = choicesAfterModel(events, eventIndex);
      const requested = choices.map((choice) => {
        const label = friendlyTool(choice.name);
        return `${label.system}: ${label.action}`;
      });
      const resumed = step === "resume";
      return [{
        event,
        kind: "model",
        title: resumed ? "Post-approval model decision" : `Model decision step ${step}`,
        description: choices.length
          ? `Requested ${requested.join(", ")} to gather or verify evidence.`
          : "The model had enough evidence and prepared the final response.",
        status: choices.length
          ? `Requested ${choices.length} ${choices.length === 1 ? "tool" : "tools"}`
          : "Prepared answer",
        details: { decision: event.payload, requested_tools: choices },
      }];
    }
    if (event.type === "tool_call") {
      const call = toolDetails(event);
      const result = toolResults.get(call.id);
      const failed = Boolean(result?.payload.is_error);
      const label = friendlyTool(call.name);
      return [{
        event,
        kind: failed ? "tool-error" : "tool",
        title: `Checked ${label.system}: ${label.action}`,
        description: failed
          ? "The tool returned an error. The model saw that result and adjusted its next step."
          : `Requested live data with ${formatArguments(call.arguments)}.`,
        status: failed ? "Error handled" : "Verified",
        details: { request: event.payload, result: result?.payload ?? null },
      }];
    }
    if (event.type === "approval_required") {
      return [{
        event,
        kind: "approval",
        title: "Paused for approval",
        description: "Highland stopped before a protected action and requested a human decision.",
        status: "Human review",
        details: event.payload,
      }];
    }
    if (["error", "run_cancelled", "run_failed"].includes(event.type)) {
      return [{
        event,
        kind: "error",
        title: event.type === "run_cancelled" ? "Run cancelled" : "Run failed",
        description: event.type === "run_failed"
          ? `${failure?.message ?? "The run stopped."} ${failure?.detail ?? ""}`
          : String(event.payload.message ?? event.payload.reason ?? "The run stopped."),
        status: "Stopped",
        details: event.payload,
      }];
    }
    if (event.type === "final") {
      return [{
        event,
        kind: "final",
        title: "Completed the grounded answer",
        description: `Synthesized the collected evidence with ${citationCount} inline citations.`,
        status: "Complete",
        details: event.payload,
      }];
    }
    return [];
  });

  return (
    <section className="agent-loop" aria-labelledby="agent-loop-heading">
      <header>
        <div>
          <p className="eyebrow">Agent loop</p>
          <h2 id="agent-loop-heading">How Highland reached this answer</h2>
          <p>Follow the model as it searches, chooses tools, observes results, and synthesizes.</p>
        </div>
        <dl className="loop-metrics">
          <div><dt>Model steps</dt><dd>{modelCount}</dd></div>
          <div><dt>Tool checks</dt><dd>{toolCount}</dd></div>
          <div><dt>Citations</dt><dd>{citationCount}</dd></div>
        </dl>
      </header>
      <ol className="trace" aria-label="Agent loop steps">
        {steps.map((step, index) => (
          <li className={`trace-${step.kind}`} key={step.event.id}>
            <span className="trace-index" aria-hidden="true">{index + 1}</span>
            <div className="trace-body">
              <header>
                <strong>{step.title}</strong>
                <span>{step.status}</span>
                <time>{new Date(step.event.timestamp).toLocaleTimeString()}</time>
              </header>
              <p>{step.description}</p>
              <details>
                <summary>Technical details</summary>
                <pre>{JSON.stringify(step.details, null, 2)}</pre>
              </details>
            </div>
          </li>
        ))}
      </ol>
    </section>
  );
}

function annotatedAnswer(answer: string, citations: Citation[]) {
  const insertions = citations
    .map((citation, index) => ({ citation, index, offset: citation.end }))
    .filter(({ citation, offset }) => (
      offset >= 0
      && offset <= answer.length
      && (!citation.text || answer.slice(citation.start, citation.end) === citation.text)
    ))
    .sort((left, right) => right.offset - left.offset || right.index - left.index);
  let markdown = answer;
  for (const insertion of insertions) {
    const marker = ` [${insertion.index + 1}](#citation-${insertion.index + 1})`;
    markdown = markdown.slice(0, insertion.offset) + marker + markdown.slice(insertion.offset);
  }
  return markdown;
}

export function CitedAnswer({
  answer,
  citations,
  selected,
  onSelect,
}: {
  answer: string;
  citations: Citation[];
  selected?: number;
  onSelect: (citationIndex: number) => void;
}) {
  return (
    <div className="markdown-answer">
      <ReactMarkdown
        components={{
          a: ({ href, children }) => {
            const match = href?.match(/^#citation-(\d+)$/);
            if (match) {
              const citationIndex = Number(match[1]) - 1;
              return (
                <button
                  aria-label={`View evidence for citation ${citationIndex + 1}`}
                  aria-pressed={selected === citationIndex}
                  className="citation-marker"
                  onClick={() => onSelect(citationIndex)}
                  title={`View evidence for citation ${citationIndex + 1}`}
                  type="button"
                >
                  {children}
                </button>
              );
            }
            return <a href={href} rel="noreferrer" target="_blank">{children}</a>;
          },
        }}
        remarkPlugins={[remarkGfm]}
      >
        {annotatedAnswer(answer, citations)}
      </ReactMarkdown>
    </div>
  );
}

function parsedToolPayload(content: unknown) {
  if (typeof content !== "string") return {};
  try {
    return asRecord(JSON.parse(content) as unknown);
  } catch {
    return {};
  }
}

function sourceRecords(payload: Record<string, unknown>) {
  for (const key of ["items", "series"]) {
    if (Array.isArray(payload[key])) {
      return (payload[key] as unknown[]).map(asRecord).filter((item) => Object.keys(item).length);
    }
  }
  return Object.keys(payload).length ? [payload] : [];
}

function matchingSourceRecord(
  records: Record<string, unknown>[],
  citation: Citation,
) {
  const identifiers = new Set(citation.source_ids);
  const byIdentifier = records.find((record) => (
    [record.id, record.source_id, record.key].some((value) => identifiers.has(String(value ?? "")))
  ));
  if (byIdentifier) return byIdentifier;
  if (citation.text) {
    const claim = citation.text.toLocaleLowerCase();
    const byClaim = records.filter((record) => (
      JSON.stringify(record).toLocaleLowerCase().includes(claim)
    ));
    if (byClaim.length === 1) return byClaim[0];
  }
  return records.length === 1 ? records[0] : undefined;
}

function isBrowsableSourceUrl(sourceUrl: string) {
  try {
    const url = new URL(sourceUrl);
    return ["http:", "https:"].includes(url.protocol)
      && !url.hostname.endsWith(".test")
      && !url.hostname.endsWith(".summit.test");
  } catch {
    return false;
  }
}

function SourceRecordAccess({
  sourceUrl,
  record,
  raw,
  ambiguousCount = 0,
}: {
  sourceUrl: string;
  record?: Record<string, unknown>;
  raw?: string;
  ambiguousCount?: number;
}) {
  const view = record ? JSON.stringify(record, null, 2) : raw;
  return (
    <div className="source-access">
      {view && (
        <details className="tool-response">
          <summary>{record ? "View source record" : "View supporting tool response"}</summary>
          <div className="tool-response-body">
            {ambiguousCount > 1 && (
              <p>
                This tool returned {ambiguousCount} records. The citation identifies the response,
                but not one exact record, so the complete response is shown.
              </p>
            )}
            <pre className="tool-response-json"><code>{view}</code></pre>
          </div>
        </details>
      )}
      {sourceUrl && (
        <p className="source-identifier">
          <b>Canonical identifier</b>
          <code>{sourceUrl}</code>
        </p>
      )}
      {sourceUrl && isBrowsableSourceUrl(sourceUrl) && (
        <a href={sourceUrl} rel="noreferrer" target="_blank">Open external source ↗</a>
      )}
      {sourceUrl && !isBrowsableSourceUrl(sourceUrl) && (
        <small>
          This demo identifier is provenance, not a public website. The saved source data is
          available above through Highland.
        </small>
      )}
    </div>
  );
}

export function CitationInspector({
  citation,
  citationNumber,
  evidence,
  events,
}: {
  citation?: Citation;
  citationNumber?: number;
  evidence: Evidence[];
  events: TraceEvent[];
}) {
  if (!citation || !citationNumber) {
    return (
      <aside className="evidence-panel evidence-empty" aria-label="Citation evidence">
        <p className="eyebrow">Why this answer?</p>
        <h2>Select a citation</h2>
        <p>Choose a numbered citation in the answer to inspect the exact evidence Highland used.</p>
      </aside>
    );
  }
  const source = evidence.find((item) => (
    citation.source_ids.includes(item.id) || citation.source_ids.includes(item.source_id)
  ));
  const toolCallId = citation.tool_call_ids?.[0];
  const toolEvent = events.find((event) => (
    event.type === "tool_call" && toolDetails(event).id === toolCallId
  ));
  const tool = toolEvent ? toolDetails(toolEvent) : undefined;
  const toolResult = events.find((event) => (
    event.type === "tool_result" && event.payload.tool_call_id === toolCallId
  ));
  const payload = parsedToolPayload(toolResult?.payload.content);
  const records = sourceRecords(payload);
  const record = matchingSourceRecord(records, citation);
  const label = tool ? friendlyTool(tool.name) : undefined;
  const sourceUrl = String(record?.source_url ?? source?.source_url ?? "");
  const sourceTitle = String(
    record?.title
      ?? record?.name
      ?? record?.key
      ?? record?.id
      ?? source?.title
      ?? (records.length > 1 ? "Tool response supporting this claim" : "Supporting evidence"),
  );

  return (
    <aside
      aria-label={`Citation ${citationNumber} evidence`}
      aria-live="polite"
      className="evidence-panel evidence-selected"
      id="citation-evidence"
      tabIndex={-1}
    >
      <p className="eyebrow">Citation {citationNumber} · {tool ? "Live tool evidence" : "Indexed source"}</p>
      <h2>{sourceTitle}</h2>
      <blockquote>{citation.text || source?.text || "Claim supported by the selected source."}</blockquote>
      <dl>
        {label && <><dt>System</dt><dd>{label.system}</dd></>}
        {label && <><dt>Tool</dt><dd>{label.action}</dd></>}
        {tool && <><dt>Request</dt><dd>{formatArguments(tool.arguments)}</dd></>}
        {source && <><dt>Section</dt><dd>{source.location.section}</dd></>}
        {source && <><dt>Source</dt><dd>{source.source_system} · {source.source_id}</dd></>}
        <dt>Result</dt><dd>{toolResult?.payload.is_error ? "Tool returned an error" : "Verified successfully"}</dd>
      </dl>
      <SourceRecordAccess
        ambiguousCount={record ? 0 : records.length}
        raw={record
          ? undefined
          : Object.keys(payload).length
            ? JSON.stringify(payload, null, 2)
            : String(toolResult?.payload.content ?? "")}
        record={record}
        sourceUrl={sourceUrl}
      />
      <p className="diagnostic-note">
        This citation is tied to {tool
          ? record
            ? "the live source record shown above"
            : "the complete live tool response shown above"
          : "the indexed passage shown above"}.
      </p>
    </aside>
  );
}

export function EvidencePanel({
  evidence,
  selectedId,
  refreshed,
}: {
  evidence: Evidence[];
  selectedId?: string;
  refreshed: boolean;
}) {
  const selected = evidence.find((item) => item.id === selectedId) ?? evidence[0];
  if (!selected) return null;
  return (
    <aside className="evidence-panel" aria-label="Evidence">
      <p className="eyebrow">Why this answer?</p>
      <h2>{selected.title}</h2>
      <blockquote>{selected.text}</blockquote>
      <dl>
        <dt>Section</dt><dd>{selected.location.section}</dd>
        <dt>Source</dt><dd>{selected.source_system} · {selected.source_id}</dd>
        <dt>Type</dt><dd>{selected.source_type}</dd>
        <dt>Customer</dt><dd>{selected.customer_id ?? "Shared"}</dd>
        <dt>Updated</dt><dd>{new Date(selected.updated_at).toLocaleString()}</dd>
      </dl>
      {refreshed && <p className="fresh-badge">Mutable fact refreshed through MCP</p>}
      <SourceRecordAccess sourceUrl={selected.source_url} />
      <p className="diagnostic-note">
        Selected because it passed the visible filters and retrieval/rerank stages shown in the trace.
      </p>
    </aside>
  );
}

export function DiscoverWorkspace() {
  const [runConversationId, setRunConversationId] = useState<string>();
  const [runId, setRunId] = useState<string>();
  const [liveRunId, setLiveRunId] = useState<string>();
  const [events, setEvents] = useState<TraceEvent[]>([]);
  const [answer, setAnswer] = useState("");
  const [error, setError] = useState<string>();
  const [streamNotice, setStreamNotice] = useState<string>();
  const [starting, setStarting] = useState(false);
  const [runs, setRuns] = useState<RunSummary[]>([]);
  const [historyLoading, setHistoryLoading] = useState(true);
  const [historyError, setHistoryError] = useState<string>();
  const [loadingRunId, setLoadingRunId] = useState<string>();
  const [runExpanded, setRunExpanded] = useState(false);
  const [selectedCitation, setSelectedCitation] = useState<number>();
  const [artifact, setArtifact] = useState<ArtifactDocument>();
  const [creatingArtifact, setCreatingArtifact] = useState(false);
  const source = useRef<EventSource>(null);
  const runOutputRef = useRef<HTMLElement>(null);
  const receivedEventIds = useRef(new Set<number>());

  const refreshRuns = useCallback(async (signal?: AbortSignal) => {
    setHistoryLoading(true);
    try {
      const response = await fetch(`${API}/runs`, { signal });
      if (!response.ok) throw new Error("Previous runs could not be loaded.");
      setRuns((await response.json()) as RunSummary[]);
      setHistoryError(undefined);
    } catch (caught) {
      if ((caught as Error).name !== "AbortError") {
        setHistoryError(
          caught instanceof Error ? caught.message : "Previous runs could not be loaded.",
        );
      }
    } finally {
      if (!signal?.aborted) setHistoryLoading(false);
    }
  }, []);

  useEffect(() => {
    const controller = new AbortController();
    void refreshRuns(controller.signal);
    return () => controller.abort();
  }, [refreshRuns]);

  useEffect(() => {
    if (!liveRunId) return;
    source.current?.close();
    const stream = new EventSource(`${API}/runs/${liveRunId}/events`);
    let terminalReceived = false;
    source.current = stream;
    stream.onopen = () => setStreamNotice(undefined);
    const receive = (message: MessageEvent) => {
      const event = JSON.parse(message.data) as TraceEvent;
      if (receivedEventIds.current.has(event.id)) return;
      receivedEventIds.current.add(event.id);
      setEvents((current) =>
        [...current, event],
      );
      if (event.type === "model_delta") {
        setAnswer((current) => current + String(event.payload.text ?? ""));
      }
      if (event.type === "error") {
        setError(String(event.payload.message ?? "The run failed. Inspect the trace for details."));
      }
      if (event.type === "final") {
        setAnswer((current) => current || String(event.payload.content ?? ""));
      }
      if (TERMINAL_EVENT_TYPES.includes(event.type)) {
        terminalReceived = true;
        setLiveRunId(undefined);
        setStreamNotice(undefined);
        stream.close();
        void refreshRuns();
      }
    };
    for (const name of [
      "run_started", "retrieval", "model_call", "model_delta", "tool_call",
      "tool_result", "approval_required", "citation", "error", "final", "run_cancelled",
      "run_completed", "run_failed",
    ]) stream.addEventListener(name, receive);
    stream.onerror = () => {
      if (!terminalReceived) {
        setStreamNotice("Connection paused. Rejoining the saved run…");
      }
    };
    return () => stream.close();
  }, [liveRunId, refreshRuns]);

  useEffect(() => {
    if (!liveRunId || !runExpanded) return;
    const timeout = window.setTimeout(() => {
      const panel = runOutputRef.current;
      panel?.focus({ preventScroll: true });
      if (panel && "scrollIntoView" in panel) {
        panel.scrollIntoView({ behavior: "smooth", block: "start" });
      }
    }, 0);
    return () => window.clearTimeout(timeout);
  }, [liveRunId, runExpanded]);

  async function restoreRun(run: RunSummary) {
    if (runId === run.run_id) {
      setRunExpanded((current) => !current);
      return;
    }
    source.current?.close();
    setLiveRunId(undefined);
    setLoadingRunId(run.run_id);
    setError(undefined);
    setStreamNotice(undefined);
    setSelectedCitation(undefined);
    setArtifact(undefined);
    try {
      const [summaryResponse, traceResponse] = await Promise.all([
        fetch(`${API}/runs/${run.run_id}/summary`),
        fetch(`${API}/runs/${run.run_id}/trace`),
      ]);
      if (!summaryResponse.ok || !traceResponse.ok) {
        throw new Error("The persisted run could not be replayed.");
      }
      const summary = (await summaryResponse.json()) as RunSummary;
      const trace = (await traceResponse.json()) as TraceEvent[];
      receivedEventIds.current = new Set(trace.map((event) => event.id));
      setEvents(trace);
      setRunId(run.run_id);
      setRunExpanded(true);
      setRunConversationId(run.conversation_id);
      const replayedAnswer = summary.final?.content
        ?? trace
          .filter((event) => event.type === "model_delta")
          .map((event) => String(event.payload.text ?? ""))
          .join("");
      setAnswer(replayedAnswer);
      if (!replayedAnswer && summary.status !== "running") {
        const errorEvent = [...trace].reverse().find((event) => event.type === "error");
        if (errorEvent) {
          setError(String(errorEvent.payload.message ?? "The run failed."));
        }
      }
      if (summary.status === "running") setLiveRunId(run.run_id);
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "The persisted run could not be replayed.");
    } finally {
      setLoadingRunId(undefined);
    }
  }

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setError(undefined);
    setStreamNotice(undefined);
    setEvents([]);
    setAnswer("");
    setSelectedCitation(undefined);
    setRunExpanded(true);
    setArtifact(undefined);
    receivedEventIds.current = new Set();
    source.current?.close();
    setLiveRunId(undefined);
    setRunId(undefined);
    setRunConversationId(undefined);
    const data = new FormData(event.currentTarget);
    const content = String(data.get("question") ?? "").trim();
    const customerId = String(data.get("customer_id") ?? "").trim();
    const sourceTypes = String(data.get("source_type") ?? "").trim();
    if (!content) {
      setError("Enter a question before starting discovery.");
      return;
    }
    setStarting(true);
    try {
      const created = await fetch(`${API}/conversations`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ title: content.slice(0, 200) }),
      });
      if (!created.ok) throw new Error("A conversation could not be created.");
      const conversation = String((await created.json()).id);
      setRunConversationId(conversation);
      const response = await fetch(`${API}/conversations/${conversation}/runs`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          content,
          filters: {
            customer_id: customerId || null,
            source_types: sourceTypes ? [sourceTypes] : [],
            allowed_visibilities: [],
          },
        }),
      });
      if (!response.ok) {
        const payload = await response.json().catch(() => ({})) as { detail?: string };
        throw new Error(payload.detail ?? "Discovery could not be started.");
      }
      const payload = await response.json() as { run_id?: string };
      if (!payload.run_id) throw new Error("The API did not return a run identifier.");
      setRunId(payload.run_id);
      setLiveRunId(payload.run_id);
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "Discovery could not be started.");
    } finally {
      setStarting(false);
    }
  }

  const citations = events
    .filter((event) => event.type === "citation")
    .map((event) => event.payload as unknown as Citation);
  const retrieval = events.find((event) => event.type === "retrieval");
  const evidence = ((retrieval?.payload.results as Array<{ chunk: Evidence }> | undefined) ?? [])
    .map((item) => item.chunk);
  const failure = runFailureInfo(events);
  const runFinished = events.some((event) => TERMINAL_EVENT_TYPES.includes(event.type));
  const runActive = Boolean(liveRunId && !runFinished);
  const runCancelled = events.some((event) => event.type === "run_cancelled");
  const runCompleted = events.some((event) => (
    event.type === "final" || event.type === "run_completed"
  ));
  const modelStepCount = events.filter((event) => event.type === "model_call").length;
  const toolCheckCount = events.filter((event) => event.type === "tool_call").length;
  const runHeading = failure
    ? "Discovery stopped"
    : runCancelled
      ? "Discovery cancelled"
      : runCompleted
        ? "Grounded answer and evidence"
        : "Discovery in progress";

  function selectCitation(citationIndex: number) {
    setSelectedCitation(citationIndex);
    window.setTimeout(() => {
      const panel = document.getElementById("citation-evidence");
      panel?.focus({ preventScroll: true });
      if (panel && "scrollIntoView" in panel) {
        panel.scrollIntoView({ behavior: "smooth", block: "nearest" });
      }
    }, 0);
  }

  async function cancel() {
    if (!runId) return;
    try {
      const response = await fetch(`${API}/runs/${runId}/cancel`, { method: "POST" });
      if (!response.ok) throw new Error("The run could not be cancelled.");
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "The run could not be cancelled.");
    }
  }

  async function turnIntoArtifact() {
    if (!runConversationId || !runId) return;
    setCreatingArtifact(true);
    try {
      const conversationResponse = await fetch(`${API}/conversations/${runConversationId}`);
      if (!conversationResponse.ok) throw new Error("The completed conversation could not be loaded.");
      const conversation = await conversationResponse.json() as {
        messages: Array<{ id: string; role: string; run_id?: string }>;
      };
      const message = conversation.messages.find(
        (item) => item.role === "assistant" && item.run_id === runId,
      );
      if (!message) throw new Error("Completed answer not found.");
      const response = await fetch(`${API}/artifacts/generate`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          artifact_type: "briefing",
          conversation_id: runConversationId,
          message_id: message.id,
        }),
      });
      if (!response.ok) throw new Error("Artifact could not be generated.");
      setArtifact((await response.json()) as ArtifactDocument);
      setError(undefined);
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "Artifact could not be generated.");
    } finally {
      setCreatingArtifact(false);
    }
  }

  return (
    <section className="welcome">
      <p className="eyebrow">Ask Highland</p>
      <h1>Investigate across your workspace.</h1>
      <p>
        Ask a question and let the workspace agent verify live facts. Each discovery starts with
        a clean model conversation.
      </p>
      <form className="prompt" onSubmit={submit}>
        <label htmlFor="question">Ask Highland</label>
        <textarea
          disabled={starting || runActive}
          id="question"
          name="question"
          placeholder="Prepare me for the Northwind customer meeting…"
          required
        />
        <SourceFilterControls />
        <div className="run-actions">
          {runActive && (
            <button className="secondary" onClick={() => void cancel()} type="button">
              Cancel run
            </button>
          )}
          <button disabled={starting || runActive} type="submit">
            {starting ? "Starting…" : runActive ? "Discovery running…" : "Start discovery"}
          </button>
        </div>
      </form>
      {error && <p className="form-error" role="alert">{error}</p>}
      {streamNotice && <p className="stream-notice" role="status">{streamNotice}</p>}
      {runExpanded && (runId || answer || events.length > 0) && (
        <section
          aria-labelledby="run-output-heading"
          className="run-output"
          ref={runOutputRef}
          tabIndex={-1}
        >
          <header>
            <div>
              <p className="eyebrow">Run output</p>
              <h2 id="run-output-heading">{runHeading}</h2>
            </div>
            <button className="secondary" onClick={() => setRunExpanded(false)} type="button">
              Collapse output
            </button>
          </header>
          {runActive && (
            <div className="run-progress" role="status">
              <span aria-hidden="true" className="run-progress-dot" />
              <div>
                <strong>Highland is investigating your question</strong>
                <p>
                  {modelStepCount} model {modelStepCount === 1 ? "step" : "steps"} · {toolCheckCount} live {toolCheckCount === 1 ? "check" : "checks"}
                </p>
              </div>
            </div>
          )}
          {failure && (
            <div className="run-failure" role="alert">
              <strong>{failure.title}</strong>
              <p>{failure.message}</p>
              <p>{failure.detail}</p>
              <details>
                <summary>Technical reason</summary>
                <code>{failure.reason}</code>
              </details>
            </div>
          )}
          {answer ? (
            <div className="answer-layout">
              <article className="streaming-answer" aria-live="polite">
                <CitedAnswer
                  answer={answer}
                  citations={citations}
                  onSelect={selectCitation}
                  selected={selectedCitation}
                />
                {events.some((event) => event.type === "final") && (
                  <button disabled={creatingArtifact} onClick={() => void turnIntoArtifact()}>
                    {creatingArtifact ? "Creating…" : "Turn into artifact"}
                  </button>
                )}
              </article>
              <CitationInspector
                citation={selectedCitation === undefined ? undefined : citations[selectedCitation]}
                citationNumber={selectedCitation === undefined ? undefined : selectedCitation + 1}
                evidence={evidence}
                events={events}
              />
            </div>
          ) : runActive ? (
            <p className="history-empty">
              The answer will appear here after Highland finishes collecting and verifying evidence.
            </p>
          ) : runCancelled ? (
            <p className="history-empty">This run was cancelled before an answer was written.</p>
          ) : !failure ? (
            <p className="history-empty">This run did not produce an answer.</p>
          ) : null}
          {events.length > 0 && <TraceTimeline events={events} />}
        </section>
      )}
      <section className="discover-history" aria-labelledby="previous-runs-heading">
        <header>
          <div>
            <p className="eyebrow">Durable history</p>
            <h2 id="previous-runs-heading">Previous runs</h2>
          </div>
          <button
            className="secondary"
            disabled={historyLoading}
            onClick={() => void refreshRuns()}
            type="button"
          >
            {historyLoading ? "Loading…" : "Refresh"}
          </button>
        </header>
        {historyError && <p className="form-error">{historyError}</p>}
        {!historyLoading && !historyError && runs.length === 0 && (
          <p className="history-empty">Completed and active runs will appear here.</p>
        )}
        {runs.length > 0 && (
          <ol>
            {runs.map((run) => (
              <li key={run.run_id}>
                <button
                  aria-expanded={runId === run.run_id ? runExpanded : false}
                  className="run-history-item"
                  disabled={loadingRunId === run.run_id}
                  onClick={() => void restoreRun(run)}
                  type="button"
                >
                  <strong>{run.prompt || "Run without a recorded prompt"}</strong>
                  <span>
                    {loadingRunId === run.run_id ? "Loading" : run.status}
                    {run.updated_at ? ` · ${new Date(run.updated_at).toLocaleString()}` : ""}
                  </span>
                  {run.final_preview && <small>{run.final_preview}</small>}
                </button>
              </li>
            ))}
          </ol>
        )}
      </section>
      {artifact && (
        <ArtifactEditor initialArtifact={artifact} onClose={() => setArtifact(undefined)} />
      )}
    </section>
  );
}
