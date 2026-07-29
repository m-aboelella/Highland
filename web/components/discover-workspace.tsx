"use client";

import { FormEvent, useEffect, useRef, useState } from "react";

type TraceEvent = {
  id: number;
  type: string;
  timestamp: string;
  payload: Record<string, unknown>;
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

export function DiscoverWorkspace() {
  const [conversationId, setConversationId] = useState<string>();
  const [runId, setRunId] = useState<string>();
  const [events, setEvents] = useState<TraceEvent[]>([]);
  const [answer, setAnswer] = useState("");
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
    let conversation = conversationId;
    if (!conversation) {
      const created = await fetch(`${API}/conversations`, { method: "POST" });
      conversation = (await created.json()).id;
      setConversationId(conversation);
    }
    const response = await fetch(`${API}/conversations/${conversation}/runs`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ content }),
    });
    setRunId((await response.json()).run_id);
  }

  async function cancel() {
    if (runId) await fetch(`${API}/runs/${runId}/cancel`, { method: "POST" });
  }

  return (
    <section className="welcome">
      <p className="eyebrow">Discover · Scripted mode is clearly labelled</p>
      <h1>Find the signal across your workspace.</h1>
      <p>Search local enterprise knowledge, inspect its sources, and ask grounded follow-ups.</p>
      <form className="prompt" onSubmit={submit}>
        <label htmlFor="question">Ask Highland</label>
        <textarea id="question" name="question" placeholder="Prepare me for the Northwind customer meeting…" />
        <div className="run-actions">
          {runId && <button className="secondary" onClick={cancel} type="button">Cancel run</button>}
          <button type="submit">Start discovery</button>
        </div>
      </form>
      {answer && <article className="streaming-answer" aria-live="polite">{answer}</article>}
      {events.length > 0 && <TraceTimeline events={events} />}
    </section>
  );
}
