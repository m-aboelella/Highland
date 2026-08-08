"use client";

import { useEffect, useMemo, useRef, useState } from "react";

import { apiUrl, requestJson } from "../lib/api";
import { ArtifactAssistantPanel } from "./artifact-assistant";
import { MarkdownPreview } from "./artifact-markdown";

export type ArtifactCitation = {
  id: string;
  label: string;
  source_id: string;
  source_url: string;
  title?: string;
  passage?: string;
  source_system?: string;
  updated_at?: string;
};

export type ArtifactDocument = {
  id: string;
  title: string;
  artifact_type: string;
  content: string;
  citations: ArtifactCitation[];
  revision: number;
  conversation_id: string;
  run_id: string;
  message_id?: string;
  updated_at: string;
};

type RevisionPreview = {
  expected_revision: number;
  heading: string;
  original_markdown: string;
  proposed_markdown: string;
  resulting_content: string;
  citations: ArtifactCitation[];
};

type CoverageReport = {
  advisory: string;
  claims: Array<{
    text: string;
    status: "supported" | "weakly_supported" | "unsupported" | "stale";
    explanation: string;
    citation_ids?: string[];
  }>;
};

type EditorView = "write" | "preview" | "split";

const coverageLabels: Record<CoverageReport["claims"][number]["status"], string> = {
  supported: "Supported",
  weakly_supported: "Needs a closer look",
  unsupported: "No saved evidence mapped",
  stale: "Source may be outdated",
};

export function ArtifactEditor({
  initialArtifact,
  onClose,
}: {
  initialArtifact: ArtifactDocument;
  onClose?: () => void;
}) {
  const [artifact, setArtifact] = useState(initialArtifact);
  const [content, setContent] = useState(initialArtifact.content);
  const [editorView, setEditorView] = useState<EditorView>("split");
  const [saveState, setSaveState] = useState<"saved" | "unsaved" | "saving">("saved");
  const [section, setSection] = useState("");
  const [revisionInstruction, setRevisionInstruction] = useState("");
  const [preview, setPreview] = useState<RevisionPreview>();
  const [revisionCount, setRevisionCount] = useState<number>();
  const [coverage, setCoverage] = useState<CoverageReport>();
  const [actionError, setActionError] = useState<string>();
  const markdownEditor = useRef<HTMLTextAreaElement>(null);
  const headings = useMemo(
    () =>
      [...content.matchAll(/^#{1,6}\s+(.+)$/gm)].map((match) => match[1].trim()),
    [content],
  );

  useEffect(() => {
    const warn = (event: BeforeUnloadEvent) => {
      if (saveState === "unsaved") event.preventDefault();
    };
    window.addEventListener("beforeunload", warn);
    return () => window.removeEventListener("beforeunload", warn);
  }, [saveState]);

  async function save(
    nextContent = content,
    nextCitations = artifact.citations,
    reason = "manual edit",
  ): Promise<ArtifactDocument | undefined> {
    setSaveState("saving");
    setActionError(undefined);
    try {
      const saved = await requestJson<ArtifactDocument>(`/artifacts/${artifact.id}`, {
        method: "PATCH",
        body: JSON.stringify({
          expected_revision: artifact.revision,
          content: nextContent,
          citations: nextCitations,
          reason,
        }),
      }, "Artifact changed elsewhere; reload before saving.");
      setArtifact(saved);
      setContent(saved.content);
      setSaveState("saved");
      setPreview(undefined);
      setCoverage(undefined);
      return saved;
    } catch (caught) {
      setSaveState("unsaved");
      setActionError(caught instanceof Error ? caught.message : "The artifact could not be saved.");
      return undefined;
    }
  }

  async function loadRevisions() {
    setActionError(undefined);
    try {
      const revisions = await requestJson<unknown[]>(
        `/artifacts/${artifact.id}/revisions`,
        {},
        "Revision history could not be loaded.",
      );
      setRevisionCount(revisions.length);
    } catch (caught) {
      setActionError(caught instanceof Error ? caught.message : "Revision history is unavailable.");
    }
  }

  async function reviseSection() {
    setActionError(undefined);
    try {
      setPreview(await requestJson<RevisionPreview>(
        `/artifacts/${artifact.id}/sections/revise`,
        {
          method: "POST",
          body: JSON.stringify({
            expected_revision: artifact.revision,
            heading: section,
            instructions: revisionInstruction,
          }),
        },
        "Section revision could not be generated.",
      ));
    } catch (caught) {
      setActionError(caught instanceof Error ? caught.message : "Section revision failed.");
    }
  }

  async function checkEvidence() {
    setActionError(undefined);
    try {
      setCoverage(await requestJson<CoverageReport>(
        `/artifacts/${artifact.id}/evidence-coverage`,
        { method: "POST" },
        "Evidence coverage could not be checked.",
      ));
    } catch (caught) {
      setActionError(caught instanceof Error ? caught.message : "Evidence coverage is unavailable.");
    }
  }

  function close() {
    if (saveState === "unsaved" && !window.confirm("Discard unsaved artifact changes?")) return;
    onClose?.();
  }

  function insertEvidenceReference(citation: ArtifactCitation) {
    const editor = markdownEditor.current;
    const start = editor?.selectionStart ?? content.length;
    const end = editor?.selectionEnd ?? content.length;
    const marker = `[${citation.id}]`;
    const needsSpace = start > 0 && !/\s/.test(content[start - 1]);
    const insertion = `${needsSpace ? " " : ""}${marker}`;
    setContent(`${content.slice(0, start)}${insertion}${content.slice(end)}`);
    setSaveState("unsaved");
    setCoverage(undefined);
    if (editorView === "preview") setEditorView("split");
    window.setTimeout(() => {
      const nextEditor = markdownEditor.current;
      const cursor = start + insertion.length;
      nextEditor?.focus();
      nextEditor?.setSelectionRange(cursor, cursor);
    }, 0);
  }

  return (
    <section className="artifact-editor" aria-label="Artifact editor">
      <header>
        <div>
          <p className="eyebrow">Saved artifact · {artifact.artifact_type.replaceAll("_", " ")}</p>
          <h2>{artifact.title}</h2>
          <p className="artifact-revision">
            Revision {artifact.revision} · Updated {new Date(artifact.updated_at).toLocaleString()}
          </p>
        </div>
        <div className="editor-actions">
          <span aria-live="polite">{saveState}</span>
          {onClose && <button className="secondary" onClick={close}>Close editor</button>}
          <button disabled={saveState !== "unsaved"} onClick={() => void save()}>
            Save revision
          </button>
        </div>
      </header>
      <p className="provenance">
        From conversation {artifact.conversation_id} ·{" "}
        <a href={apiUrl(`/runs/${artifact.run_id}/trace`)}>View originating run</a>
        {" · "}
        <a href={apiUrl(`/artifacts/${artifact.id}/export.md`)}>Export Markdown</a>
        {" · "}
        <a href={apiUrl(`/artifacts/${artifact.id}/export.pdf`)}>Export PDF</a>
      </p>
      <div className="artifact-explainer" role="note">
        <span className="artifact-explainer-icon" aria-hidden="true">A</span>
        <div>
          <strong>This is a new saved document—not another discovery run.</strong>
          <p>
            Highland reshaped the completed answer into a reusable {artifact.artifact_type.replaceAll("_", " ")}
            {" "}and carried its saved evidence forward. The wording may begin similarly, but this copy has its
            own revisions and can be edited or exported without changing the original discovery answer.
          </p>
        </div>
      </div>
      {actionError && <p className="form-error" role="alert">{actionError}</p>}
      <div className="artifact-view-switcher" aria-label="Editor view">
        {(["write", "preview", "split"] as const).map((view) => (
          <button
            aria-pressed={editorView === view}
            className={editorView === view ? "active" : ""}
            key={view}
            onClick={() => setEditorView(view)}
            type="button"
          >
            {view === "write" ? "Write" : view === "preview" ? "Preview" : "Split view"}
          </button>
        ))}
      </div>
      <div className={`artifact-document artifact-document-${editorView}`}>
        {editorView !== "preview" && (
          <div className="artifact-pane artifact-write-pane">
            <div className="artifact-pane-label">
              <strong>Markdown</strong>
              <span>Use plain text with Markdown formatting</span>
            </div>
            <textarea
              aria-label="Artifact Markdown"
              ref={markdownEditor}
              value={content}
              onChange={(event) => {
                setContent(event.target.value);
                setSaveState("unsaved");
                setCoverage(undefined);
              }}
            />
          </div>
        )}
        {editorView !== "write" && (
          <div className="artifact-pane artifact-preview-pane">
            <div className="artifact-pane-label">
              <strong>Document preview</strong>
              <span>Formatted reading view; PDF pages may differ</span>
            </div>
            <MarkdownPreview evidence={artifact.citations}>{content}</MarkdownPreview>
          </div>
        )}
      </div>
      <ArtifactAssistantPanel artifact={artifact} onApply={save} saveState={saveState} />
      <section className="artifact-evidence-library" aria-labelledby="artifact-evidence-heading">
        <header>
          <div>
            <p className="eyebrow">Evidence library</p>
            <h3 id="artifact-evidence-heading">Sources saved with this artifact</h3>
          </div>
          <span>{artifact.citations.length} {artifact.citations.length === 1 ? "source" : "sources"}</span>
        </header>
        <p>
          <strong>E means evidence.</strong> E1 is “Evidence 1,” not a user. A marker such as [E1]
          connects a sentence in the Markdown to the first source card below.
        </p>
        <div className="artifact-evidence-cards">
          {artifact.citations.map((citation) => (
            <article id={`evidence-${citation.id}`} key={citation.id}>
              <header>
                <span className="evidence-id">{citation.id}</span>
                <div>
                  <strong>Evidence {citation.label}</strong>
                  <h4>{citation.title ?? citation.source_id}</h4>
                </div>
              </header>
              {citation.passage && <blockquote>{citation.passage}</blockquote>}
              <dl>
                <dt>Source</dt>
                <dd>{citation.source_system ?? "saved discovery source"}</dd>
                <dt>Record</dt>
                <dd>{citation.source_id}</dd>
                {citation.updated_at && <><dt>Updated</dt><dd>{new Date(citation.updated_at).toLocaleDateString()}</dd></>}
              </dl>
              <div>
                <button className="secondary" onClick={() => insertEvidenceReference(citation)}>
                  Insert [{citation.id}] at cursor
                </button>
                {citation.source_url && (
                  <a href={citation.source_url} rel="noreferrer" target="_blank">Open source ↗</a>
                )}
              </div>
            </article>
          ))}
          {!artifact.citations.length && (
            <div className="empty-state">
              <strong>No evidence was saved with this artifact.</strong>
              <span>The assistant can reorganize the document, but it should not invent new factual claims.</span>
            </div>
          )}
        </div>
      </section>
      <div className="artifact-workbench">
        <section className="artifact-tool-card" aria-labelledby="section-edit-heading">
          <header>
            <div>
              <p className="eyebrow">Optional AI edit</p>
              <h3 id="section-edit-heading">Suggest a change to one section</h3>
            </div>
            <span className="tool-step">Preview before applying</span>
          </header>
          <p>Choose a section and describe the change. Highland will not replace it until you approve the suggestion.</p>
          <div className="artifact-tools">
            <label>
              Section
              <select value={section} onChange={(event) => setSection(event.target.value)}>
                <option value="">Select a section</option>
                {headings.map((heading) => <option key={heading}>{heading}</option>)}
              </select>
            </label>
            <label className="revision-instruction">
              What should change?
              <input
                placeholder="For example: make this shorter and lead with the decision"
                value={revisionInstruction}
                onChange={(event) => setRevisionInstruction(event.target.value)}
              />
            </label>
            <button
              disabled={!section || !revisionInstruction}
              onClick={() => void reviseSection()}
            >
              Preview suggested change
            </button>
          </div>
        </section>
        <section className="artifact-tool-card" aria-labelledby="claim-support-heading">
          <header>
            <div>
              <p className="eyebrow">Optional review</p>
              <h3 id="claim-support-heading">Review claim support</h3>
            </div>
            <span className="tool-step">Saved revision only</span>
          </header>
          <p>
            Highland compares factual claims with evidence saved from discovery. “No saved evidence mapped” means
            the claim needs a citation or rewrite—it does not mean the source itself is unsupported.
          </p>
          <div className="artifact-review-actions">
            <button
              className="secondary"
              disabled={saveState !== "saved"}
              onClick={() => void checkEvidence()}
            >
              Review saved claims
            </button>
            {saveState !== "saved" && <small>Save this revision before reviewing its claims.</small>}
          </div>
        </section>
      </div>
      <button className="artifact-revisions secondary" onClick={() => void loadRevisions()}>
        Revision history {revisionCount === undefined ? "" : `(${revisionCount})`}
      </button>
      {preview && (
        <aside className="revision-preview" aria-label="Section revision preview">
          <header>
            <div>
              <p className="eyebrow">Suggested change</p>
              <h3>{preview.heading}</h3>
            </div>
            <span>Nothing has been replaced yet</span>
          </header>
          <MarkdownPreview evidence={preview.citations}>{preview.proposed_markdown}</MarkdownPreview>
          <div className="revision-preview-actions">
            <button onClick={() => void save(preview.resulting_content, preview.citations)}>
              Apply and save section
            </button>
            <button className="secondary" onClick={() => setPreview(undefined)}>Discard suggestion</button>
          </div>
        </aside>
      )}
      {coverage && (
        <aside className="coverage-report" aria-label="Evidence coverage">
          <header>
            <div>
              <p className="eyebrow">Review result</p>
              <h3>Claim support in revision {artifact.revision}</h3>
            </div>
            <span>{coverage.claims.length} {coverage.claims.length === 1 ? "claim" : "claims"} reviewed</span>
          </header>
          <p>{coverage.advisory}</p>
          {coverage.claims.length ? (
            <ul>
              {coverage.claims.map((claim, index) => (
                <li className={`coverage-${claim.status}`} key={`${claim.text}-${index}`}>
                  <b>{coverageLabels[claim.status]}</b>
                  <q>{claim.text}</q>
                  {claim.citation_ids?.length ? (
                    <small>Saved evidence: {claim.citation_ids.map((id) => `[${id}]`).join(", ")}</small>
                  ) : null}
                  <small>{claim.explanation}</small>
                </li>
              ))}
            </ul>
          ) : <p>No factual claims were identified in this revision.</p>}
        </aside>
      )}
    </section>
  );
}
