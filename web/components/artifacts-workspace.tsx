"use client";

import { useEffect, useState } from "react";

import { ArtifactDocument, ArtifactEditor } from "./artifact-editor";

const API = process.env.NEXT_PUBLIC_HIGHLAND_API_URL ?? "http://127.0.0.1:8080";

export function ArtifactsWorkspace() {
  const [artifacts, setArtifacts] = useState<ArtifactDocument[]>([]);
  const [selected, setSelected] = useState<ArtifactDocument>();

  useEffect(() => {
    fetch(`${API}/artifacts`)
      .then((response) => response.json())
      .then(setArtifacts)
      .catch(() => undefined);
  }, []);

  if (selected) {
    return <ArtifactEditor initialArtifact={selected} onClose={() => setSelected(undefined)} />;
  }
  return (
    <section className="welcome">
      <p className="eyebrow">Create</p>
      <h1>Artifacts that keep their evidence.</h1>
      <p>Open a persistent document or turn a completed answer into a new one.</p>
      <div className="artifact-list">
        {artifacts.map((artifact) => (
          <button key={artifact.id} onClick={() => setSelected(artifact)}>
            <span>{artifact.title}</span>
            <small>{artifact.artifact_type.replaceAll("_", " ")} · revision {artifact.revision}</small>
          </button>
        ))}
        {!artifacts.length && <p>No artifacts yet. Create one from a completed answer.</p>}
      </div>
    </section>
  );
}
