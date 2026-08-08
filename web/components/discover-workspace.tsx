"use client";

import { FormEvent, useEffect, useRef, useState } from "react";
import {
  Citation,
  Evidence,
  TERMINAL_EVENT_TYPES,
  TraceEvent,
} from "../features/discover/types";
import {
  runFailureInfo,
  TraceTimeline,
} from "../features/discover/trace";
import {
  CitationInspector,
  CitedAnswer,
  EvidencePanel,
} from "../features/discover/evidence";
import { useDiscoverRuns } from "../features/discover/use-discover-runs";
import { requestJson } from "../lib/api";
import { ArtifactDocument, ArtifactEditor } from "./artifact-editor";
import { SourceFilterControls } from "./source-filter-controls";

export { CitationInspector, CitedAnswer, EvidencePanel } from "../features/discover/evidence";
export { TraceTimeline } from "../features/discover/trace";
export type { TraceEvent } from "../features/discover/types";

export function DiscoverWorkspace() {
  const {
    answer,
    cancelRun,
    error,
    events,
    historyError,
    historyLoading,
    liveRunId,
    loadingRunId,
    refreshRuns,
    restoreRun,
    runConversationId,
    runExpanded,
    runId,
    runs,
    setError,
    setRunExpanded,
    startDiscovery,
    starting,
    streamNotice,
  } = useDiscoverRuns();
  const [selectedCitation, setSelectedCitation] = useState<number>();
  const [artifact, setArtifact] = useState<ArtifactDocument>();
  const [creatingArtifact, setCreatingArtifact] = useState(false);
  const [artifactError, setArtifactError] = useState<string>();
  const runOutputRef = useRef<HTMLElement>(null);

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

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setSelectedCitation(undefined);
    setArtifact(undefined);
    setArtifactError(undefined);
    const data = new FormData(event.currentTarget);
    const content = String(data.get("question") ?? "").trim();
    const customerId = String(data.get("customer_id") ?? "").trim();
    const sourceType = String(data.get("source_type") ?? "").trim();
    if (!content) {
      setError("Enter a question before starting discovery.");
      return;
    }
    await startDiscovery({ content, customerId, sourceType });
  }

  const citations = events
    .filter((event) => event.type === "citation")
    .map((event) => event.payload as unknown as Citation);
  const retrieval = events.find((event) => event.type === "retrieval");
  const evidence = ((retrieval?.payload.results as Array<{ chunk: Evidence }> | undefined) ?? [])
    .map((item) => item.chunk);
  const failure = runFailureInfo(events);
  const runFinished = events.some((event) => (
    TERMINAL_EVENT_TYPES.some((terminalType) => terminalType === event.type)
  ));
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

  async function turnIntoArtifact() {
    if (!runConversationId || !runId) return;
    setCreatingArtifact(true);
    setArtifactError(undefined);
    try {
      const conversation = await requestJson<{
        messages: Array<{ id: string; role: string; run_id?: string }>;
      }>(
        `/conversations/${runConversationId}`,
        {},
        "The completed conversation could not be loaded.",
      );
      const message = conversation.messages.find(
        (item) => item.role === "assistant" && item.run_id === runId,
      );
      if (!message) throw new Error("Completed answer not found.");
      const createdArtifact = await requestJson<ArtifactDocument>("/artifacts/generate", {
        method: "POST",
        body: JSON.stringify({
          artifact_type: "briefing",
          conversation_id: runConversationId,
          message_id: message.id,
        }),
      }, "Artifact could not be generated.");
      setArtifact(createdArtifact);
    } catch (caught) {
      setArtifactError(
        caught instanceof Error ? caught.message : "Artifact could not be generated.",
      );
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
            <button className="secondary" onClick={() => void cancelRun()} type="button">
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
                  <div className="artifact-create-action">
                    <p>
                      Need a reusable document? Create a separate, editable briefing from this answer and its saved evidence.
                    </p>
                    <button disabled={creatingArtifact} onClick={() => void turnIntoArtifact()}>
                      {creatingArtifact ? "Creating briefing…" : "Create saved briefing"}
                    </button>
                    {artifactError && <p className="form-error" role="alert">{artifactError}</p>}
                  </div>
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
                  onClick={() => {
                    setSelectedCitation(undefined);
                    setArtifact(undefined);
                    setArtifactError(undefined);
                    void restoreRun(run);
                  }}
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
