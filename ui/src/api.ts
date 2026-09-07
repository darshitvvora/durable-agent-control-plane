import type { AgentPackage, FleetStatus, Job, Lane, RampStatus, SessionState, Tenant } from "./types";

/** The API is the only thing the UI talks to — never Temporal directly (CLAUDE.md §2). */
async function get<T>(path: string): Promise<T> {
  const response = await fetch(path);
  if (!response.ok) throw new Error(`${path} -> ${response.status}`);
  return response.json() as Promise<T>;
}

async function post<T>(path: string): Promise<T> {
  const response = await fetch(path, { method: "POST" });
  if (!response.ok) throw new Error(`${path} -> ${response.status}`);
  return response.json() as Promise<T>;
}

export const api = {
  tenants: () => get<Tenant[]>("/api/tenants"),
  agents: () => get<AgentPackage[]>("/api/agents"),
  lanes: () => get<Lane[]>("/api/metrics/lanes"),
  status: () => get<FleetStatus>("/api/metrics/status"),
  jobs: (tenantId: string) => get<Job[]>(`/api/jobs?tenant_id=${encodeURIComponent(tenantId)}`),
  job: (jobId: string) => get<Job>(`/api/jobs/${encodeURIComponent(jobId)}`),
  jobState: (jobId: string) => get<SessionState>(`/api/jobs/${encodeURIComponent(jobId)}/state`),
  runSession: (agentId: string, tenantId: string, prompt: string) =>
    post<{ job_id: string }>(
      `/api/jobs?agent_id=${encodeURIComponent(agentId)}&tenant_id=${encodeURIComponent(
        tenantId,
      )}&prompt=${encodeURIComponent(prompt)}`,
    ),

  install: (tenantId: string, agentId: string) =>
    post<Tenant>(
      `/api/tenants/${encodeURIComponent(tenantId)}/install?agent_id=${encodeURIComponent(agentId)}`,
    ),
  uninstall: (tenantId: string, agentId: string) =>
    post<Tenant>(
      `/api/tenants/${encodeURIComponent(tenantId)}/uninstall?agent_id=${encodeURIComponent(agentId)}`,
    ),

  // Demo controls. Each one mutates real Temporal or AWS state — see
  // SystemControls.tsx for what each proves.
  setFairness: (enabled: boolean) => post<{ enabled: boolean }>(`/api/demo/fairness?enabled=${enabled}`),
  flood: (tenantId: string, count: number) =>
    post<{ submitted: number }>(
      `/api/demo/flood?tenant_id=${encodeURIComponent(tenantId)}&count=${count}`,
    ),
  setRamp: (buildId: string, percentage: number) =>
    post<RampStatus>(
      `/api/demo/ramp?build_id=${encodeURIComponent(buildId)}&percentage=${percentage}`,
    ),
  clearRamp: () => post<RampStatus>("/api/demo/ramp/clear"),
  killWorker: () => post<{ armed: boolean }>("/api/demo/kill-worker"),
  approve: (jobId: string, interruptId: string, decision: string) =>
    post<unknown>(
      `/api/jobs/${encodeURIComponent(jobId)}/approval?interrupt_id=${encodeURIComponent(
        interruptId,
      )}&decision=${encodeURIComponent(decision)}`,
    ),
};

export const eventsUrl = (jobId: string) => `/api/jobs/${encodeURIComponent(jobId)}/events`;
