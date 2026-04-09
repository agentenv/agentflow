export interface Pipeline {
  name: string;
  nodes: NodeSpec[];
  scratchboard?: boolean;
}

export interface NodeSpec {
  id: string;
  kind: string;
  depends_on?: string[];
  [key: string]: any;
}

export interface Run {
  id: string;
  pipeline: Pipeline;
  status: 'pending' | 'queued' | 'running' | 'completed' | 'failed' | 'cancelled' | 'cancelling';
  nodes?: Record<string, NodeState>;
  started_at?: string;
  finished_at?: string;
  created_at?: string;
}

export interface NodeState {
  node_id: string;
  status: 'pending' | 'queued' | 'running' | 'completed' | 'failed' | 'cancelled' | 'cancelling' | 'retrying' | 'skipped';
  exit_code?: number;
  output?: string | any;
  current_attempt: number;
  attempts?: any[];
  trace_events?: any[];
  started_at?: string;
  finished_at?: string;
}

export const fetchRuns = async (): Promise<Run[]> => {
  const res = await fetch(`/api/runs?t=${Date.now()}`);
  if (!res.ok) throw new Error('Failed to fetch runs');
  return res.json();
};

export const fetchRun = async (id: string): Promise<Run> => {
  const res = await fetch(`/api/runs/${id}?t=${Date.now()}`);
  if (!res.ok) throw new Error('Failed to fetch run');
  return res.json();
};

export const fetchScratchboard = async (runId: string): Promise<string> => {
  const res = await fetch(`/api/runs/${runId}/scratchboard?t=${Date.now()}`);
  if (!res.ok) throw new Error('Failed to fetch scratchboard');
  return res.text();
};

export const cancelRun = async (runId: string) => {
  const res = await fetch(`/api/runs/${runId}/cancel`, { method: 'POST' });
  if (!res.ok) throw new Error('Failed to cancel run');
  return res.json();
};

export const rerunRun = async (runId: string) => {
  const res = await fetch(`/api/runs/${runId}/rerun`, { method: 'POST' });
  if (!res.ok) throw new Error('Failed to rerun pipeline');
  return res.json();
};
export interface Health {
  ok: boolean;
  runs: {
    total: number;
    queued: number;
    running: number;
  };
}

export const fetchHealth = async (): Promise<Health> => {
  const res = await fetch(`/api/health?t=${Date.now()}`);
  if (!res.ok) throw new Error('Failed to fetch health');
  return res.json();
};

export const fetchArtifactContent = async (runId: string, nodeId: string, name: string): Promise<string> => {
  const res = await fetch(`/api/runs/${runId}/artifacts/${nodeId}/${name}?t=${Date.now()}`);
  if (!res.ok) throw new Error('Artifact not found');
  return res.text();
};
