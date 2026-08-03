"use client";

import { FormEvent, useState } from "react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";

import { SourceFilterControls } from "./source-filter-controls";

const API = process.env.NEXT_PUBLIC_HIGHLAND_API_URL ?? "http://127.0.0.1:8080";

const sourceLabels: Record<string, string> = {
  archive: "Archive",
  relay: "Relay Desk",
  beacon: "Beacon",
  pulse: "Pulse",
  track: "Track",
};

type SearchResult = {
  score: number;
  chunk: {
    id: string;
    source_system: string;
    source_id: string;
    title: string;
    text: string;
    source_type: string;
    source_url: string;
    customer_id?: string;
    updated_at: string;
    location: { section: string };
  };
};

type SearchResponse = {
  query: string;
  results: SearchResult[];
  timings: { total_ms: number };
  rerank_model?: string;
};

export function SearchWorkspace() {
  const [response, setResponse] = useState<SearchResponse>();
  const [searching, setSearching] = useState(false);
  const [error, setError] = useState<string>();

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const data = new FormData(event.currentTarget);
    const query = String(data.get("query") ?? "").trim();
    const customerId = String(data.get("customer_id") ?? "").trim();
    const sourceType = String(data.get("source_type") ?? "").trim();
    if (!query) {
      setError("Enter a search term.");
      return;
    }
    setSearching(true);
    setError(undefined);
    try {
      const result = await fetch(`${API}/discover/search`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          query,
          filters: {
            customer_id: customerId || null,
            source_types: sourceType ? [sourceType] : [],
            allowed_visibilities: [],
          },
        }),
      });
      if (!result.ok) {
        const payload = await result.json().catch(() => ({})) as { detail?: string };
        throw new Error(payload.detail ?? "Source search could not be completed.");
      }
      setResponse(await result.json() as SearchResponse);
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "Source search could not be completed.");
    } finally {
      setSearching(false);
    }
  }

  return (
    <section className="welcome search-workspace">
      <p className="eyebrow">Source search</p>
      <h1>Find the evidence directly.</h1>
      <p>
        Search returns ranked passages from Highland&apos;s index. It does not start a conversation,
        ask the chat model to write an answer, or call live tools.
      </p>
      <form className="prompt search-prompt" onSubmit={submit}>
        <label htmlFor="source-query">Search indexed knowledge</label>
        <input
          id="source-query"
          name="query"
          placeholder="retrieval latency, Northwind renewal, incident policy…"
          type="search"
        />
        <SourceFilterControls />
        <button disabled={searching} type="submit">
          {searching ? "Searching…" : "Search sources"}
        </button>
      </form>
      {error && <p className="form-error" role="alert">{error}</p>}
      {response && (
        <section className="search-results" aria-labelledby="search-results-heading">
          <header>
            <div>
              <p className="eyebrow">Ranked evidence</p>
              <h2 id="search-results-heading">
                {response.results.length} {response.results.length === 1 ? "passage" : "passages"}
              </h2>
            </div>
            <span>{Math.round(response.timings.total_ms)} ms</span>
          </header>
          {!response.results.length && (
            <div className="empty-state">
              <strong>No matching passages.</strong>
              <span>Try broader terms or remove an optional source limit.</span>
            </div>
          )}
          <ol>
            {response.results.map((result, index) => (
              <li key={result.chunk.id}>
                <article className="search-result">
                  <header>
                    <span>#{index + 1}</span>
                    <span>{sourceLabels[result.chunk.source_system] ?? result.chunk.source_system}</span>
                    <span>{result.chunk.source_type.replaceAll("_", " ")}</span>
                  </header>
                  <h3>{result.chunk.title}</h3>
                  <p className="result-section">{result.chunk.location.section}</p>
                  <div className="markdown-answer"><ReactMarkdown remarkPlugins={[remarkGfm]}>{result.chunk.text}</ReactMarkdown></div>
                  <dl>
                    <dt>Relevance</dt><dd>{result.score.toFixed(3)}</dd>
                    <dt>Customer</dt><dd>{result.chunk.customer_id ?? "Shared"}</dd>
                    <dt>Updated</dt><dd>{new Date(result.chunk.updated_at).toLocaleString()}</dd>
                  </dl>
                  <details className="source-access">
                    <summary>Source provenance</summary>
                    <p className="source-identifier">
                      <b>Canonical identifier</b>
                      <code>{result.chunk.source_url}</code>
                    </p>
                    <small>
                      Demo source identifiers record where evidence came from; they are not public websites.
                    </small>
                  </details>
                </article>
              </li>
            ))}
          </ol>
        </section>
      )}
    </section>
  );
}
