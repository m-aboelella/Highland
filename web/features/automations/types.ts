export type WorkflowNode = { id: string; name: string; kind: string };

export type Workflow = {
  id: string;
  name: string;
  description: string;
  nodes: WorkflowNode[];
  edges: Array<{ source: string; target: string }>;
};

export type WorkflowVersion = {
  workflow_id: string;
  version: number;
  definition: Workflow;
  published_at: string;
};

export type PublishedWorkflow = {
  workflow_id: string;
  name: string;
  description: string;
  latest_version: number;
  version_count: number;
  published_at: string;
  step_count: number;
  active_schedule_count: number;
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
  test?: boolean;
  workflow_snapshot?: Workflow | null;
  published_version?: number | null;
};
