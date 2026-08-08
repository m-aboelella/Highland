"use client";

import { TraceEvent } from "./types";

const SOURCE_LABELS: Record<string, string> = {
  crm: "Atlas CRM",
  knowledge: "Archive",
  support: "Relay Desk",
  observability: "Beacon",
  communications: "Pulse",
  projects: "Track",
};

export function asRecord(value: unknown): Record<string, unknown> {
  return value && typeof value === "object" && !Array.isArray(value)
    ? value as Record<string, unknown>
    : {};
}

export function toolDetails(event: TraceEvent) {
  const call = asRecord(event.payload.tool_call);
  return {
    id: String(call.id ?? ""),
    name: String(call.name ?? "tool"),
    arguments: asRecord(call.arguments),
  };
}

type ToolChoice = ReturnType<typeof toolDetails>;

function embeddedToolChoices(event: TraceEvent): ToolChoice[] | undefined {
  if (!("tool_calls" in event.payload)) return undefined;
  if (!Array.isArray(event.payload.tool_calls)) return [];
  return event.payload.tool_calls.map((item) => {
    const call = asRecord(item);
    return {
      id: String(call.id ?? ""),
      name: String(call.name ?? "tool"),
      arguments: asRecord(call.arguments),
    };
  });
}

function choicesAfterModel(events: TraceEvent[], modelIndex: number) {
  const embedded = embeddedToolChoices(events[modelIndex]);
  if (embedded !== undefined) return embedded;
  const choices: ToolChoice[] = [];
  for (const event of events.slice(modelIndex + 1)) {
    if (["model_call", "final", "error", "run_cancelled"].includes(event.type)) break;
    if (event.type === "tool_call") choices.push(toolDetails(event));
  }
  return choices;
}

export function friendlyTool(toolName: string) {
  const [system, action = toolName] = toolName.split("__", 2);
  return {
    system: SOURCE_LABELS[system] ?? system.replaceAll("_", " "),
    action: action.replaceAll("_", " "),
  };
}

export function formatArguments(arguments_: Record<string, unknown>) {
  const entries = Object.entries(arguments_);
  if (!entries.length) return "No filters";
  return entries
    .map(([name, value]) => `${name.replaceAll("_", " ")}: ${String(value)}`)
    .join(" · ");
}

function cleanToolError(content: unknown) {
  return String(content ?? "The tool returned an error.")
    .replace(/^Error executing tool [^:]+:\s*/i, "")
    .replace(/^\w+ returned HTTP \d+:\s*/i, "");
}

export function runFailureInfo(events: TraceEvent[]) {
  const terminal = [...events].reverse().find((event) => (
    event.type === "run_failed" || event.type === "error"
  ));
  if (!terminal) return undefined;
  const modelCalls = events.filter((event) => event.type === "model_call").length;
  const failedResult = [...events].reverse().find((event) => (
    event.type === "tool_result" && Boolean(event.payload.is_error)
  ));
  const failedCall = failedResult
    ? events.find((event) => (
        event.type === "tool_call"
        && toolDetails(event).id === String(failedResult.payload.tool_call_id ?? "")
      ))
    : undefined;
  const failedTool = failedCall ? friendlyTool(toolDetails(failedCall).name) : undefined;
  const reason = String(terminal.payload.reason ?? terminal.payload.message ?? "The run stopped.");
  const budgetReached = reason.includes("maximum steps") || reason.includes("runtime budget");
  return {
    title: budgetReached
      ? "Highland reached its run limit before writing the answer"
      : "Discovery stopped before writing the answer",
    message: budgetReached
      ? `The agent used all ${modelCalls} available model calls while collecting and verifying evidence.`
      : reason,
    detail: failedResult
      ? `${failedTool ? `${failedTool.system}: ${failedTool.action}` : "The last tool check"} failed with “${cleanToolError(failedResult.payload.content)}”. ${budgetReached ? "Highland attempted to recover, but the run limit was reached before it could synthesize a final response." : "Open the agent loop below to see how Highland handled the failed check."}`
      : "Open the agent loop below to see the last completed step.",
    reason,
  };
}

export function TraceTimeline({ events }: { events: TraceEvent[] }) {
  const toolResults = new Map(
    events
      .filter((event) => event.type === "tool_result")
      .map((event) => [String(event.payload.tool_call_id ?? ""), event]),
  );
  const citationCount = events.filter((event) => event.type === "citation").length;
  const modelCount = events.filter((event) => event.type === "model_call").length;
  const toolCount = events.filter((event) => event.type === "tool_call").length;
  const failure = runFailureInfo(events);
  const steps = events.flatMap((event, eventIndex) => {
    if (event.type === "retrieval") {
      const results = Array.isArray(event.payload.results) ? event.payload.results.length : 0;
      return [{
        event,
        kind: "retrieval",
        title: "Searched indexed knowledge",
        description: `Retrieved ${results} relevant passages before the agent loop began.`,
        status: "Context",
        details: event.payload,
      }];
    }
    if (event.type === "model_call") {
      const step = String(event.payload.step ?? modelCount);
      const choices = choicesAfterModel(events, eventIndex);
      const requested = choices.map((choice) => {
        const label = friendlyTool(choice.name);
        return `${label.system}: ${label.action}`;
      });
      const resumed = step === "resume";
      return [{
        event,
        kind: "model",
        title: resumed ? "Post-approval model decision" : `Model decision step ${step}`,
        description: choices.length
          ? `Requested ${requested.join(", ")} to gather or verify evidence.`
          : "The model had enough evidence and prepared the final response.",
        status: choices.length
          ? `Requested ${choices.length} ${choices.length === 1 ? "tool" : "tools"}`
          : "Prepared answer",
        details: { decision: event.payload, requested_tools: choices },
      }];
    }
    if (event.type === "tool_call") {
      const call = toolDetails(event);
      const result = toolResults.get(call.id);
      const failed = Boolean(result?.payload.is_error);
      const label = friendlyTool(call.name);
      return [{
        event,
        kind: failed ? "tool-error" : "tool",
        title: `Checked ${label.system}: ${label.action}`,
        description: failed
          ? "The tool returned an error. The model saw that result and adjusted its next step."
          : `Requested live data with ${formatArguments(call.arguments)}.`,
        status: failed ? "Error handled" : "Verified",
        details: { request: event.payload, result: result?.payload ?? null },
      }];
    }
    if (event.type === "approval_required") {
      return [{
        event,
        kind: "approval",
        title: "Paused for approval",
        description: "Highland stopped before a protected action and requested a human decision.",
        status: "Human review",
        details: event.payload,
      }];
    }
    if (["error", "run_cancelled", "run_failed"].includes(event.type)) {
      return [{
        event,
        kind: "error",
        title: event.type === "run_cancelled" ? "Run cancelled" : "Run failed",
        description: event.type === "run_failed"
          ? `${failure?.message ?? "The run stopped."} ${failure?.detail ?? ""}`
          : String(event.payload.message ?? event.payload.reason ?? "The run stopped."),
        status: "Stopped",
        details: event.payload,
      }];
    }
    if (event.type === "final") {
      return [{
        event,
        kind: "final",
        title: "Completed the grounded answer",
        description: `Synthesized the collected evidence with ${citationCount} inline citations.`,
        status: "Complete",
        details: event.payload,
      }];
    }
    return [];
  });

  return (
    <section className="agent-loop" aria-labelledby="agent-loop-heading">
      <header>
        <div>
          <p className="eyebrow">Agent loop</p>
          <h2 id="agent-loop-heading">How Highland reached this answer</h2>
          <p>Follow the model as it searches, chooses tools, observes results, and synthesizes.</p>
        </div>
        <dl className="loop-metrics">
          <div><dt>Model steps</dt><dd>{modelCount}</dd></div>
          <div><dt>Tool checks</dt><dd>{toolCount}</dd></div>
          <div><dt>Citations</dt><dd>{citationCount}</dd></div>
        </dl>
      </header>
      <ol className="trace" aria-label="Agent loop steps">
        {steps.map((step, index) => (
          <li className={`trace-${step.kind}`} key={step.event.id}>
            <span className="trace-index" aria-hidden="true">{index + 1}</span>
            <div className="trace-body">
              <header>
                <strong>{step.title}</strong>
                <span>{step.status}</span>
                <time>{new Date(step.event.timestamp).toLocaleTimeString()}</time>
              </header>
              <p>{step.description}</p>
              <details>
                <summary>Technical details</summary>
                <pre>{JSON.stringify(step.details, null, 2)}</pre>
              </details>
            </div>
          </li>
        ))}
      </ol>
    </section>
  );
}
