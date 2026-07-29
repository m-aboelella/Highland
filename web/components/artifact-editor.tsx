"use client";

import { useEffect, useMemo, useState } from "react";

const API = process.env.NEXT_PUBLIC_HIGHLAND_API_URL ?? "http://127.0.0.1:8080";

export type ArtifactCitation = {
  id: string;
  label: string;
  source_id: string;
  source_url: string;
  title?: string;
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
  }>;
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
  const [saveState, setSaveState] = useState<"saved" | "unsaved" | "saving">("saved");
  const [section, setSection] = useState("");
  const [revisionInstruction, setRevisionInstruction] = useState("");
  const [preview, setPreview] = useState<RevisionPreview>();
  const [revisionCount, setRevisionCount] = useState<number>();
  const [coverage, setCoverage] = useState<CoverageReport>();
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

  async function save(nextContent = content, nextCitations = artifact.citations) {
    setSaveState("saving");
    const response = await fetch(`${API}/artifacts/${artifact.id}`, {
      method: "PATCH",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        expected_revision: artifact.revision,
        content: nextContent,
        citations: nextCitations,
      }),
    });
    if (!response.ok) {
      setSaveState("unsaved");
      throw new Error("Artifact changed elsewhere; reload before saving.");
    }
    const saved = (await response.json()) as ArtifactDocument;
    setArtifact(saved);
    setContent(saved.content);
    setSaveState("saved");
    setPreview(undefined);
  }

  async function loadRevisions() {
    const response = await fetch(`${API}/artifacts/${artifact.id}/revisions`);
    const revisions = (await response.json()) as unknown[];
    setRevisionCount(revisions.length);
  }

  async function reviseSection() {
    const response = await fetch(`${API}/artifacts/${artifact.id}/sections/revise`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        expected_revision: artifact.revision,
        heading: section,
        instructions: revisionInstruction,
      }),
    });
    if (!response.ok) throw new Error("Section revision could not be generated.");
    setPreview((await response.json()) as RevisionPreview);
  }

  async function checkEvidence() {
    const response = await fetch(`${API}/artifacts/${artifact.id}/evidence-coverage`, {
      method: "POST",
    });
    if (!response.ok) throw new Error("Evidence coverage could not be checked.");
    setCoverage((await response.json()) as CoverageReport);
  }

  function close() {
    if (saveState === "unsaved" && !window.confirm("Discard unsaved artifact changes?")) return;
    onClose?.();
  }

  return (
    <section className="artifact-editor" aria-label="Artifact editor">
      <header>
        <div>
          <p className="eyebrow">Create · {artifact.artifact_type.replaceAll("_", " ")}</p>
          <h2>{artifact.title}</h2>
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
        <a href={`${API}/runs/${artifact.run_id}/trace`}>View originating run</a>
      </p>
      <textarea
        aria-label="Artifact Markdown"
        value={content}
        onChange={(event) => {
          setContent(event.target.value);
          setSaveState("unsaved");
        }}
      />
      <div className="artifact-tools">
        <label>
          Section
          <select value={section} onChange={(event) => setSection(event.target.value)}>
            <option value="">Select a section</option>
            {headings.map((heading) => <option key={heading}>{heading}</option>)}
          </select>
        </label>
        <label>
          Revision instruction
          <input
            value={revisionInstruction}
            onChange={(event) => setRevisionInstruction(event.target.value)}
          />
        </label>
        <button
          disabled={!section || !revisionInstruction}
          onClick={() => void reviseSection()}
        >
          Preview section edit
        </button>
        <button className="secondary" onClick={() => void loadRevisions()}>
          Revisions {revisionCount === undefined ? "" : `(${revisionCount})`}
        </button>
        <button className="secondary" onClick={() => void checkEvidence()}>
          Check evidence
        </button>
      </div>
      <div className="citation-insert">
        <span>Insert citation:</span>
        {artifact.citations.map((citation) => (
          <button
            className="secondary"
            key={citation.id}
            title={citation.title ?? citation.source_id}
            onClick={() => {
              setContent((current) => `${current} [${citation.id}]`);
              setSaveState("unsaved");
            }}
          >
            [{citation.id}]
          </button>
        ))}
      </div>
      {preview && (
        <aside className="revision-preview" aria-label="Section revision preview">
          <h3>Preview: {preview.heading}</h3>
          <pre>{preview.proposed_markdown}</pre>
          <button
            onClick={() => void save(preview.resulting_content, preview.citations)}
          >
            Replace this section
          </button>
          <button className="secondary" onClick={() => setPreview(undefined)}>Discard preview</button>
        </aside>
      )}
      {coverage && (
        <aside className="coverage-report" aria-label="Evidence coverage">
          <h3>Claim and evidence coverage</h3>
          <p>{coverage.advisory}</p>
          <ul>
            {coverage.claims.map((claim, index) => (
              <li className={`coverage-${claim.status}`} key={`${claim.text}-${index}`}>
                <b>{claim.status.replaceAll("_", " ")}</b> {claim.text}
                <small>{claim.explanation}</small>
              </li>
            ))}
          </ul>
        </aside>
      )}
    </section>
  );
}
