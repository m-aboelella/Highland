"use client";

import Link from "next/link";
import { useCallback, useEffect, useState } from "react";

import { requestJson } from "../lib/api";

type AgentProfile = {
  id: string;
  instructions: string;
  allowed_tools: string[];
  retrieval_defaults: { top_k: number };
  budgets: {
    max_steps: number;
    max_model_calls: number;
    max_wall_seconds: number;
    max_tool_result_chars: number;
    max_context_chars: number;
  };
  models: { chat: string; embedding: string; rerank: string };
};

export function AgentsWorkspace() {
  const [profile, setProfile] = useState<AgentProfile>();
  const [state, setState] = useState<"loading" | "ready" | "error">("loading");

  const load = useCallback(async () => {
    setState("loading");
    try {
      const profiles = await requestJson<AgentProfile[]>(
        "/agents",
        {},
        "Agent configuration could not be loaded.",
      );
      if (!profiles.length) throw new Error("No agent profile is configured.");
      setProfile(profiles[0]);
      setState("ready");
    } catch {
      setState("error");
    }
  }, []);

  useEffect(() => { void load(); }, [load]);

  return (
    <section className="welcome agent-workspace">
      <p className="eyebrow">Active agent configuration</p>
      <h1>Understand what powers each run.</h1>
      <p>
        Highland currently uses one built-in workspace agent for every conversation and
        automation. This page explains its real configuration; it is not an agent builder.
      </p>
      <aside className="read-only-note">
        <strong>Read-only in the UI</strong>
        <span>
          Creating, editing, and switching profiles is not available yet. Repository operators
          can change the checked-in agent configuration and restart Highland.
        </span>
      </aside>
      {state === "loading" && (
        <div className="empty-state"><strong>Loading the active profile…</strong></div>
      )}
      {state === "error" && (
        <div className="empty-state" role="alert">
          <strong>The active agent is unavailable.</strong>
          <span>Check the Highland API, then try again.</span>
          <button className="secondary" onClick={() => void load()}>Try again</button>
        </div>
      )}
      {state === "ready" && profile && (
        <article className="agent-profile">
          <header>
            <div>
              <span className="active-badge">Active</span>
              <h2>General workspace agent</h2>
              <p>Profile ID: <code>{profile.id}</code></p>
            </div>
            <Link className="button-link" href="/">Start a run with this agent</Link>
          </header>
          <div className="agent-grid">
            <section className="agent-card">
              <h3>Instructions</h3>
              <p>{profile.instructions}</p>
            </section>
            <section className="agent-card">
              <h3>Chat model</h3>
              <p>{profile.models.chat}</p>
              <small>Embedding: {profile.models.embedding}<br />Rerank: {profile.models.rerank}</small>
            </section>
            <section className="agent-card">
              <h3>Tool access</h3>
              <p>{profile.allowed_tools.includes("*")
                ? "All configured connector tools, still governed by validation and approval rules."
                : profile.allowed_tools.join(", ")}</p>
            </section>
            <section className="agent-card">
              <h3>Retrieval</h3>
              <p>Up to {profile.retrieval_defaults.top_k} passages are supplied as grounded context.</p>
            </section>
            <section className="agent-card agent-budget-card">
              <h3>Run limits</h3>
              <dl>
                <dt>Model calls</dt><dd>{profile.budgets.max_model_calls}</dd>
                <dt>Agent steps</dt><dd>{profile.budgets.max_steps}</dd>
                <dt>Wall time</dt><dd>{profile.budgets.max_wall_seconds} seconds</dd>
              </dl>
            </section>
          </div>
        </article>
      )}
    </section>
  );
}
