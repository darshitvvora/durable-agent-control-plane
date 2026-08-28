export type Tenant = {
  tenant_id: string;
  name: string;
  tier: string;
  priority_key: number;
  fairness_weight: number;
  memory_namespace: string;
  s3_prefix: string;
  installed_agent_ids: string[];
};

export type AgentPackage = {
  agent_id: string;
  version: number;
  name: string;
  tier: string;
  model: string;
  tools: string[];
  approval_policy: { tool: string; field: string; operator: string; value: number } | null;
};

export type Lane = {
  tenant_id: string;
  name: string;
  tier: string;
  priority_key: number;
  fairness_weight: number;
  /** null when the TenantId search attribute is not registered yet — unknown, not zero. */
  running: number | null;
  queued: number;
  p95_wait_seconds: number | null;
};

export type RampStatus = {
  current_version: string;
  ramping_version: string;
  ramping_percentage: number;
};

export type FleetStatus = {
  workers: number;
  jobs_by_status: Record<string, number>;
  fairness_enabled: boolean;
  ramp: RampStatus;
  payment_count: number;
};

export type Job = {
  job_id: string;
  tenant_id: string;
  agent_id: string;
  status: string;
  created_at: string;
  started_at: string | null;
};

export type SessionState = {
  job_id: string;
  status: string;
  worker_version: string | null;
  pending_approval: {
    interrupt_id: string;
    tool: string;
    tool_input: Record<string, unknown>;
    policy: string;
  } | null;
};

/** One line in the session terminal, from the SSE stream. */
export type SessionLine =
  | { kind: "token"; text: string }
  | { kind: "reasoning"; text: string }
  | { kind: "tool_call"; tool: string; tool_use_id: string }
  | { kind: "job_started"; payload: Record<string, unknown> }
  | { kind: "job_finished"; payload: Record<string, unknown> }
  | { kind: "approval_pending"; payload: Record<string, unknown> }
  | { kind: "approval_resumed"; payload: Record<string, unknown> };
