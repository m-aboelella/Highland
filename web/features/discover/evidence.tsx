"use client";

import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";

import { Citation, Evidence, TraceEvent } from "./types";
import { asRecord, formatArguments, friendlyTool, toolDetails } from "./trace";

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
