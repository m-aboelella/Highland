export type TraceEvent = {
  id: number;
  type: string;
  timestamp: string;
  payload: Record<string, unknown>;
};

export type RunSummary = {
  run_id: string;
  status: string;
  event_count: number;
  last_event_id: number;
  started_at?: string;
  updated_at?: string;
  conversation_id?: string;
  prompt?: string;
  final?: { content?: string };
  final_preview?: string;
};

export type Citation = {
  start: number;
  end: number;
  text?: string;
  source_ids: string[];
  tool_call_ids?: string[];
};

export type Evidence = {
  id: string;
  source_id: string;
  title: string;
  text: string;
  source_system: string;
  source_type: string;
  source_url: string;
  customer_id?: string;
  updated_at: string;
  location: { section: string };
};

export const TERMINAL_EVENT_TYPES = [
  "final",
  "error",
  "run_cancelled",
  "run_completed",
  "run_failed",
] as const;
