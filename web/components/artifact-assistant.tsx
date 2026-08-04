"use client";

import { FormEvent, useEffect, useRef, useState } from "react";

import type { ArtifactCitation, ArtifactDocument } from "./artifact-editor";
import { MarkdownPreview } from "./artifact-markdown";

const API = process.env.NEXT_PUBLIC_HIGHLAND_API_URL ?? "http://127.0.0.1:8080";

type AssistantOperation = {
  tool: "read_artifact" | "read_saved_evidence" | "propose_markdown_edit" | "write_artifact_revision";
  label: string;
  detail: string;
  status: "completed" | "awaiting_approval";
};

type AssistantPreview = {
  artifact_id: string;
  expected_revision: number;
  assistant_message: string;
  summary: string;
  proposed_content: string;
  citations: ArtifactCitation[];
  operations: AssistantOperation[];
  model: string;
};

type ChatMessage = {
  id: string;
  role: "user" | "assistant";
  content: string;
  operations?: AssistantOperation[];
  model?: string;
};

const examples = [
  "Make the executive summary shorter and more decisive.",
  "Add a concise next steps section using the saved evidence.",
  "Turn the key dates and owners into a Markdown table.",
];

export function ArtifactAssistantPanel({
  artifact,
  saveState,
  onApply,
}: {
  artifact: ArtifactDocument;
  saveState: "saved" | "unsaved" | "saving";
  onApply: (
    content: string,
    citations: ArtifactCitation[],
    reason: string,
  ) => Promise<ArtifactDocument | undefined>;
}) {
  const [messages, setMessages] = useState<ChatMessage[]>([{
    id: "welcome",
    role: "assistant",
    content: (
      "Tell me how you want to change this artifact. I can read this Markdown file and its saved "
      + "evidence, then stage a change for you to review. I cannot alter the original discovery run."
    ),
  }]);
  const [instruction, setInstruction] = useState("");
  const [preview, setPreview] = useState<AssistantPreview>();
  const [working, setWorking] = useState(false);
  const [error, setError] = useState<string>();
  const chatEnd = useRef<HTMLDivElement>(null);

  useEffect(() => {
    chatEnd.current?.scrollIntoView?.({ behavior: "smooth", block: "nearest" });
  }, [messages, working]);

  async function submit(event: FormEvent) {
    event.preventDefault();
    const nextInstruction = instruction.trim();
    if (!nextInstruction || working || saveState !== "saved") return;
    const userMessage: ChatMessage = {
      id: `user-${Date.now()}`,
      role: "user",
      content: nextInstruction,
    };
    setMessages((current) => [...current, userMessage]);
    setInstruction("");
    setWorking(true);
    setError(undefined);
    try {
      const history = messages
        .filter((message) => message.id !== "welcome")
        .slice(-12)
        .map(({ role, content }) => ({ role, content }));
      const response = await fetch(`${API}/artifacts/${artifact.id}/assistant/preview`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          expected_revision: artifact.revision,
          instruction: nextInstruction,
          history,
          draft_content: preview?.proposed_content,
        }),
      });
      const payload = await response.json() as AssistantPreview | { detail?: string };
      if (!response.ok) {
        throw new Error("detail" in payload && payload.detail
          ? payload.detail
          : "The artifact assistant could not prepare an edit.");
      }
      const result = payload as AssistantPreview;
      setPreview(result);
      setMessages((current) => [...current, {
        id: `assistant-${Date.now()}`,
        role: "assistant",
        content: result.assistant_message,
        operations: result.operations,
        model: result.model,
      }]);
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "The artifact assistant is unavailable.");
    } finally {
      setWorking(false);
    }
  }

  async function apply() {
    if (!preview) return;
    const saved = await onApply(
      preview.proposed_content,
      preview.citations,
      `AI artifact assistant: ${preview.summary}`,
    );
    if (!saved) return;
    setMessages((current) => [...current, {
      id: `assistant-applied-${Date.now()}`,
      role: "assistant",
      content: `Applied the approved Markdown and wrote revision ${saved.revision}.`,
      operations: [{
        tool: "write_artifact_revision",
        label: "Write a new revision",
        detail: `Saved revision ${saved.revision} without changing the originating discovery run.`,
        status: "completed",
      }],
    }]);
    setPreview(undefined);
  }

  function discardDraft() {
    if (!preview) return;
    setPreview(undefined);
    setMessages((current) => [...current, {
      id: `assistant-discarded-${Date.now()}`,
      role: "assistant",
      content: `Discarded the working draft. Saved revision ${artifact.revision} is unchanged. What would you like to try next?`,
    }]);
  }

  function newConversation() {
    setMessages([{
      id: `welcome-${Date.now()}`,
      role: "assistant",
      content: (
        "Started a fresh artifact editing conversation. Tell me what you want to change; "
        + "the saved file stays untouched until you approve a draft."
      ),
    }]);
    setInstruction("");
    setPreview(undefined);
    setError(undefined);
  }

  const suggestions = preview ? [
    "Make this working draft shorter.",
    "Keep the wording, but improve the Markdown structure.",
    "Check that every factual claim still has saved evidence.",
  ] : examples;

  return (
    <section className="artifact-assistant" aria-labelledby="artifact-assistant-heading">
      <header>
        <div>
          <p className="eyebrow">Artifact-scoped Cohere agent</p>
          <h3 id="artifact-assistant-heading">Work on this artifact together</h3>
          <p>Chat through multiple revisions · refine a working draft · save only when ready</p>
        </div>
        <div className="artifact-assistant-header-actions">
          <span className="artifact-scope-badge">Artifact only</span>
          <button className="secondary" disabled={working} onClick={newConversation} type="button">
            New conversation
          </button>
        </div>
      </header>
      <div className={`artifact-assistant-layout ${preview ? "has-working-draft" : ""}`}>
        <section className="artifact-conversation-pane" aria-label="Artifact editing conversation">
          <div className="artifact-chat-log" aria-live="polite">
            {messages.map((message, messageIndex) => (
              <article className={`artifact-chat-message artifact-chat-${message.role}`} key={message.id}>
                <span>{message.role === "assistant" ? "Highland" : "You"}</span>
                <p>{message.content}</p>
                {message.model && <small>Prepared with {message.model}</small>}
                {message.operations && (
                  <details className="artifact-operation-details" open={messageIndex === messages.length - 1}>
                    <summary>{message.operations.length} tool steps</summary>
                    <ol className="artifact-operation-trace" aria-label="Artifact tool operations">
                      {message.operations.map((operation, index) => (
                        <li className={`operation-${operation.status}`} key={`${operation.tool}-${index}`}>
                          <span aria-hidden="true">{operation.status === "completed" ? "✓" : index + 1}</span>
                          <div>
                            <strong>{operation.label}</strong>
                            <small>{operation.detail}</small>
                          </div>
                        </li>
                      ))}
                    </ol>
                  </details>
                )}
              </article>
            ))}
            {working && (
              <article className="artifact-chat-message artifact-chat-assistant artifact-chat-working" role="status">
                <span>Highland</span>
                <p>{preview ? "Refining our working draft…" : "Reading the artifact and preparing our first draft…"}</p>
              </article>
            )}
            <div ref={chatEnd} />
          </div>
          {error && <p className="form-error" role="alert">{error}</p>}
          <form className="artifact-chat-form" onSubmit={submit}>
            <label htmlFor="artifact-assistant-instruction">
              {preview ? "Ask for another change" : "Message Highland about this artifact"}
            </label>
            <textarea
              disabled={working}
              id="artifact-assistant-instruction"
              onChange={(event) => setInstruction(event.target.value)}
              placeholder={preview
                ? "For example: keep that structure, but make the opening more concise…"
                : "For example: make the summary shorter and add a next-steps table…"}
              value={instruction}
            />
            <div className="artifact-chat-examples" aria-label="Example edit requests">
              {suggestions.map((example) => (
                <button className="secondary" key={example} onClick={() => setInstruction(example)} type="button">
                  {example}
                </button>
              ))}
            </div>
            <div className="artifact-chat-submit">
              {saveState !== "saved" && <small>Save your manual draft before continuing the conversation.</small>}
              <button disabled={!instruction.trim() || working || saveState !== "saved"} type="submit">
                {working ? "Highland is editing…" : "Send"}
              </button>
            </div>
          </form>
        </section>
        <aside className="assistant-working-draft" aria-label="Conversational working draft">
          <header>
            <div>
              <p className="eyebrow">Working draft</p>
              <h4>{preview ? `Based on saved revision ${preview.expected_revision}` : "No draft yet"}</h4>
            </div>
            <span>{preview ? "Unsaved" : "Start chatting"}</span>
          </header>
          {preview ? (
            <>
              <MarkdownPreview evidence={preview.citations}>{preview.proposed_content}</MarkdownPreview>
              <div className="assistant-preview-actions">
                <button onClick={() => void apply()}>Apply and save revision {preview.expected_revision + 1}</button>
                <button className="secondary" onClick={discardDraft}>Discard working draft</button>
              </div>
            </>
          ) : (
            <div className="assistant-draft-empty">
              <span aria-hidden="true">✦</span>
              <strong>Your shared draft will appear here</strong>
              <p>Keep chatting to refine it. Nothing is written to the artifact until you approve.</p>
            </div>
          )}
        </aside>
      </div>
    </section>
  );
}
