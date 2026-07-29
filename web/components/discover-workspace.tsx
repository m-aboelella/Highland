"use client";

import { FormEvent, useEffect, useRef, useState } from "react";

import { ArtifactDocument, ArtifactEditor } from "./artifact-editor";

export type TraceEvent = {
  id: number;
  type: string;
  timestamp: string;
  payload: Record<string, unknown>;
};

type Citation = {
  start: number;
  end: number;
  source_ids: string[];
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

export function TraceTimeline({ events }: { events: TraceEvent[] }) {
  return (
    <ol className="trace" aria-label="Execution trace">
      {events.map((event) => (
        <li key={event.id}>
          <span>{event.type.replaceAll("_", " ")}</span>
          <time>{new Date(event.timestamp).toLocaleTimeString()}</time>
          <details>
            <summary>Inspect event</summary>
            <pre>{JSON.stringify(event.payload, null, 2)}</pre>
          </details>
        </li>
      ))}
    </ol>
  );
}

export function CitedAnswer({
  answer,
  citations,
  onSelect,
}: {
  answer: string;
  citations: Citation[];
  onSelect: (chunkId: string) => void;
}) {
  if (!citations.length) return <mark className="unsupported">{answer}</mark>;
  const ordered = [...citations].sort((left, right) => left.start - right.start);
  let cursor = 0;
  return (
    <>
      {ordered.map((citation, index) => {
        const prefix = answer.slice(cursor, citation.start);
        const claim = answer.slice(citation.start, citation.end);
        cursor = citation.end;
        return (
          <span key={`${citation.start}-${index}`}>
            {prefix && <mark className="unsupported">{prefix}</mark>}
            <span className="supported">{claim}</span>
            <button
              className="citation-marker"
              onClick={() => onSelect(citation.source_ids[0])}
              type="button"
            >
              {index + 1}
            </button>
          </span>
        );
      })}
      {cursor < answer.length && <mark className="unsupported">{answer.slice(cursor)}</mark>}
    </>
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
      <a href={selected.source_url}>Open canonical source</a>
      <p className="diagnostic-note">
        Selected because it passed the visible filters and retrieval/rerank stages shown in the trace.
      </p>
    </aside>
  );
}

export function DiscoverWorkspace() {
  const [conversationId, setConversationId] = useState<string>();
  const [runId, setRunId] = useState<string>();
  const [events, setEvents] = useState<TraceEvent[]>([]);
  const [answer, setAnswer] = useState("");
  const [selectedEvidence, setSelectedEvidence] = useState<string>();
  const [artifact, setArtifact] = useState<ArtifactDocument>();
  const [creatingArtifact, setCreatingArtifact] = useState(false);
  const source = useRef<EventSource>(null);

  useEffect(() => {
    if (!runId) return;
    source.current?.close();
    const stream = new EventSource(`${API}/runs/${runId}/events`);
    source.current = stream;
    const receive = (message: MessageEvent) => {
      const event = JSON.parse(message.data) as TraceEvent;
      setEvents((current) =>
        current.some((item) => item.id === event.id) ? current : [...current, event],
      );
      if (event.type === "model_delta") {
        setAnswer((current) => current + String(event.payload.text ?? ""));
      }
      if (["final", "error", "run_cancelled"].includes(event.type)) stream.close();
    };
    for (const name of [
      "run_started", "retrieval", "model_call", "model_delta", "tool_call",
      "tool_result", "approval_required", "citation", "error", "final", "run_cancelled",
    ]) stream.addEventListener(name, receive);
    return () => stream.close();
  }, [runId]);

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setEvents([]);
    setAnswer("");
    const data = new FormData(event.currentTarget);
    const content = String(data.get("question") ?? "");
    const customerId = String(data.get("customer_id") ?? "").trim();
    const sourceTypes = String(data.get("source_type") ?? "").trim();
    let conversation = conversationId;
    if (!conversation) {
      const created = await fetch(`${API}/conversations`, { method: "POST" });
      conversation = (await created.json()).id;
      setConversationId(conversation);
    }
    const response = await fetch(`${API}/conversations/${conversation}/runs`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        content,
        filters: {
          customer_id: customerId || null,
          source_types: sourceTypes ? [sourceTypes] : [],
          allowed_visibilities: ["internal", "shared"],
        },
      }),
    });
    setRunId((await response.json()).run_id);
  }

  const citations = events
    .filter((event) => event.type === "citation")
    .map((event) => event.payload as unknown as Citation);
  const retrieval = events.find((event) => event.type === "retrieval");
  const evidence = ((retrieval?.payload.results as Array<{ chunk: Evidence }> | undefined) ?? [])
    .map((item) => item.chunk);
  const refreshed = events.some((event) => event.type === "tool_result");

  async function cancel() {
    if (runId) await fetch(`${API}/runs/${runId}/cancel`, { method: "POST" });
  }

  async function turnIntoArtifact() {
    if (!conversationId || !runId) return;
    setCreatingArtifact(true);
    try {
      const conversationResponse = await fetch(`${API}/conversations/${conversationId}`);
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
          conversation_id: conversationId,
          message_id: message.id,
        }),
      });
      if (!response.ok) throw new Error("Artifact could not be generated.");
      setArtifact((await response.json()) as ArtifactDocument);
    } finally {
      setCreatingArtifact(false);
    }
  }

  return (
    <section className="welcome">
      <p className="eyebrow">Discover · Scripted mode is clearly labelled</p>
      <h1>Find the signal across your workspace.</h1>
      <p>Search local enterprise knowledge, inspect its sources, and ask grounded follow-ups.</p>
      <form className="prompt" onSubmit={submit}>
        <label htmlFor="question">Ask Highland</label>
        <textarea id="question" name="question" placeholder="Prepare me for the Northwind customer meeting…" />
        <fieldset className="filters">
          <legend>Current filters</legend>
          <label>Customer <input name="customer_id" placeholder="cus_northwind" /></label>
          <label>Source type <input name="source_type" placeholder="runbook" /></label>
          <span>Visibility: internal + shared</span>
        </fieldset>
        <div className="run-actions">
          {runId && <button className="secondary" onClick={cancel} type="button">Cancel run</button>}
          <button type="submit">Start discovery</button>
        </div>
      </form>
      {answer && (
        <div className="answer-layout">
          <article className="streaming-answer" aria-live="polite">
            <CitedAnswer answer={answer} citations={citations} onSelect={setSelectedEvidence} />
            {events.some((event) => event.type === "final") && (
              <button disabled={creatingArtifact} onClick={() => void turnIntoArtifact()}>
                {creatingArtifact ? "Creating…" : "Turn into artifact"}
              </button>
            )}
          </article>
          <EvidencePanel
            evidence={evidence}
            selectedId={selectedEvidence}
            refreshed={refreshed}
          />
        </div>
      )}
      {artifact && (
        <ArtifactEditor initialArtifact={artifact} onClose={() => setArtifact(undefined)} />
      )}
      {events.length > 0 && <TraceTimeline events={events} />}
    </section>
  );
}
