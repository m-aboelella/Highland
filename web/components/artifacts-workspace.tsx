"use client";

import { useCallback, useEffect, useState } from "react";

import { ArtifactDocument, ArtifactEditor } from "./artifact-editor";

const API = process.env.NEXT_PUBLIC_HIGHLAND_API_URL ?? "http://127.0.0.1:8080";

export function ArtifactsWorkspace() {
  const [artifacts, setArtifacts] = useState<ArtifactDocument[]>([]);
  const [selected, setSelected] = useState<ArtifactDocument>();
  const [state, setState] = useState<"loading" | "ready" | "error">("loading");

  const loadArtifacts = useCallback(async () => {
    setState("loading");
    try {
      const response = await fetch(`${API}/artifacts`);
      if (!response.ok) throw new Error("Artifacts could not be loaded.");
      setArtifacts(await response.json() as ArtifactDocument[]);
      setState("ready");
    } catch {
      setState("error");
    }
  }, []);

  useEffect(() => {
    void loadArtifacts();
  }, [loadArtifacts]);

  if (selected) {
    return <ArtifactEditor initialArtifact={selected} onClose={() => setSelected(undefined)} />;
  }

  return (
    <section className="welcome">
      <p className="eyebrow">Create</p>
      <h1>Artifacts that keep their evidence.</h1>
      <p>
        An artifact is a separate, persistent document created from a completed discovery answer.
        It keeps the saved evidence, then gives you a versioned place to edit, preview, and export the result.
      </p>
      <div className="artifact-list" aria-busy={state === "loading"}>
        {state === "loading" && (
          <div className="empty-state">
            <strong>Loading artifacts…</strong>
            <span>Reading the local workspace.</span>
          </div>
        )}
        {state === "error" && (
          <div className="empty-state" role="alert">
            <strong>Artifacts are unavailable.</strong>
            <span>Check that the Highland API is running, then try again.</span>
            <button className="secondary" onClick={() => void loadArtifacts()}>Try again</button>
          </div>
        )}
        {state === "ready" && artifacts.map((artifact) => (
          <button key={artifact.id} onClick={() => setSelected(artifact)}>
            <span>{artifact.title}</span>
            <small>{artifact.artifact_type.replaceAll("_", " ")} · revision {artifact.revision}</small>
          </button>
        ))}
        {state === "ready" && !artifacts.length && (
          <div className="empty-state">
            <strong>No artifacts yet.</strong>
            <span>Complete a discovery answer, then choose “Create saved briefing.”</span>
          </div>
        )}
      </div>
    </section>
  );
}
