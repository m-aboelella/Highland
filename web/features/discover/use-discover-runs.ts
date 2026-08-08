"use client";

import { useCallback, useEffect, useRef, useState } from "react";

import { apiUrl, requestJson } from "../../lib/api";
import { RunSummary, TERMINAL_EVENT_TYPES, TraceEvent } from "./types";

type StartDiscovery = {
  content: string;
  customerId: string;
  sourceType: string;
};

export function useDiscoverRuns() {
  const [runConversationId, setRunConversationId] = useState<string>();
  const [runId, setRunId] = useState<string>();
  const [liveRunId, setLiveRunId] = useState<string>();
  const [events, setEvents] = useState<TraceEvent[]>([]);
  const [answer, setAnswer] = useState("");
  const [error, setError] = useState<string>();
  const [streamNotice, setStreamNotice] = useState<string>();
  const [starting, setStarting] = useState(false);
  const [runs, setRuns] = useState<RunSummary[]>([]);
  const [historyLoading, setHistoryLoading] = useState(true);
  const [historyError, setHistoryError] = useState<string>();
  const [loadingRunId, setLoadingRunId] = useState<string>();
  const [runExpanded, setRunExpanded] = useState(false);
  const source = useRef<EventSource>(null);
  const receivedEventIds = useRef(new Set<number>());

  const refreshRuns = useCallback(async (signal?: AbortSignal) => {
    setHistoryLoading(true);
    try {
      setRuns(await requestJson<RunSummary[]>(
        "/runs",
        { signal },
        "Previous runs could not be loaded.",
      ));
      setHistoryError(undefined);
    } catch (caught) {
      if ((caught as Error).name !== "AbortError") {
        setHistoryError(
          caught instanceof Error ? caught.message : "Previous runs could not be loaded.",
        );
      }
    } finally {
      if (!signal?.aborted) setHistoryLoading(false);
    }
  }, []);

  useEffect(() => {
    const controller = new AbortController();
    void refreshRuns(controller.signal);
    return () => controller.abort();
  }, [refreshRuns]);

  useEffect(() => {
    if (!liveRunId) return;
    source.current?.close();
    const stream = new EventSource(apiUrl(`/runs/${liveRunId}/events`));
    let terminalReceived = false;
    source.current = stream;
    stream.onopen = () => setStreamNotice(undefined);
    const receive = (message: MessageEvent) => {
      const event = JSON.parse(message.data) as TraceEvent;
      if (receivedEventIds.current.has(event.id)) return;
      receivedEventIds.current.add(event.id);
      setEvents((current) => [...current, event]);
      if (event.type === "model_delta") {
        setAnswer((current) => current + String(event.payload.text ?? ""));
      }
      if (event.type === "error") {
        setError(String(event.payload.message ?? "The run failed. Inspect the trace for details."));
      }
      if (event.type === "final") {
        setAnswer((current) => current || String(event.payload.content ?? ""));
      }
      if (TERMINAL_EVENT_TYPES.includes(event.type as typeof TERMINAL_EVENT_TYPES[number])) {
        terminalReceived = true;
        setLiveRunId(undefined);
        setStreamNotice(undefined);
        stream.close();
        void refreshRuns();
      }
    };
    for (const name of [
      "run_started", "retrieval", "model_call", "model_delta", "tool_call",
      "tool_result", "approval_required", "citation", "error", "final", "run_cancelled",
      "run_completed", "run_failed",
    ]) stream.addEventListener(name, receive);
    stream.onerror = () => {
      if (!terminalReceived) setStreamNotice("Connection paused. Rejoining the saved run…");
    };
    return () => stream.close();
  }, [liveRunId, refreshRuns]);

  async function restoreRun(run: RunSummary) {
    if (runId === run.run_id) {
      setRunExpanded((current) => !current);
      return;
    }
    source.current?.close();
    setLiveRunId(undefined);
    setLoadingRunId(run.run_id);
    setError(undefined);
    setStreamNotice(undefined);
    try {
      const [summary, trace] = await Promise.all([
        requestJson<RunSummary>(
          `/runs/${run.run_id}/summary`, {}, "The persisted run could not be replayed.",
        ),
        requestJson<TraceEvent[]>(
          `/runs/${run.run_id}/trace`, {}, "The persisted run could not be replayed.",
        ),
      ]);
      receivedEventIds.current = new Set(trace.map((event) => event.id));
      setEvents(trace);
      setRunId(run.run_id);
      setRunExpanded(true);
      setRunConversationId(run.conversation_id);
      const replayedAnswer = summary.final?.content
        ?? trace
          .filter((event) => event.type === "model_delta")
          .map((event) => String(event.payload.text ?? ""))
          .join("");
      setAnswer(replayedAnswer);
      if (!replayedAnswer && summary.status !== "running") {
        const errorEvent = [...trace].reverse().find((event) => event.type === "error");
        if (errorEvent) setError(String(errorEvent.payload.message ?? "The run failed."));
      }
      if (summary.status === "running") setLiveRunId(run.run_id);
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "The persisted run could not be replayed.");
    } finally {
      setLoadingRunId(undefined);
    }
  }

  async function startDiscovery({ content, customerId, sourceType }: StartDiscovery) {
    setError(undefined);
    setStreamNotice(undefined);
    setEvents([]);
    setAnswer("");
    setRunExpanded(true);
    receivedEventIds.current = new Set();
    source.current?.close();
    setLiveRunId(undefined);
    setRunId(undefined);
    setRunConversationId(undefined);
    setStarting(true);
    try {
      const conversation = await requestJson<{ id: string }>(
        "/conversations",
        { method: "POST", body: JSON.stringify({ title: content.slice(0, 200) }) },
        "A conversation could not be created.",
      );
      setRunConversationId(conversation.id);
      const run = await requestJson<{ run_id?: string }>(
        `/conversations/${conversation.id}/runs`,
        {
          method: "POST",
          body: JSON.stringify({
            content,
            filters: {
              customer_id: customerId || null,
              source_types: sourceType ? [sourceType] : [],
              allowed_visibilities: [],
            },
          }),
        },
        "Discovery could not be started.",
      );
      if (!run.run_id) throw new Error("The API did not return a run identifier.");
      setRunId(run.run_id);
      setLiveRunId(run.run_id);
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "Discovery could not be started.");
    } finally {
      setStarting(false);
    }
  }

  async function cancelRun() {
    if (!runId) return;
    try {
      await requestJson(
        `/runs/${runId}/cancel`,
        { method: "POST" },
        "The run could not be cancelled.",
      );
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "The run could not be cancelled.");
    }
  }

  return {
    answer,
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
    cancelRun,
  };
}
