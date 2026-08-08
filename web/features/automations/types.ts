export type WorkflowNode = { id: string; name: string; kind: string };

export type Workflow = {
  id: string;
  name: string;
  description: string;
  nodes: WorkflowNode[];
  edges: Array<{ source: string; target: string }>;
};

export type WorkflowRun = {
  id: string;
  workflow_id: string;
  workflow_version: number;
  status: string;
  error?: string | null;
  nodes: Record<string, {
    node_id?: string;
    status: string;
    output?: unknown;
    error?: string | null;
    duration_ms?: number | null;
  }>;
  model_calls?: number;
  tool_calls?: number;
  started_at?: string;
  updated_at?: string;
};
