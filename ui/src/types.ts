export type Tenant = {
  tenant_id: string;
  name: string;
  tier: string;
  /** Temporal priority tier, 1-5, lower = served first. Coarse. */
  priority_key: number;
  /** Share of the queue within a tier when tenants contend — weight 3 gets
   *  roughly 3x the throughput of weight 1. This is the knob proof 1 shows. */
  fairness_weight: number;
  /** Tenant isolation: its own AgentCore Memory scope and its own S3 prefix,
   *  so one tenant can never recall or read another's data. */
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

/** One tenant's row in the process monitor. */
export type Lane = {
  tenant_id: string;
  name: string;
  tier: string;
  priority_key: number;
  fairness_weight: number;
  /** null when the TenantId search attribute is not registered yet — unknown, not zero. */
  running: number | null;
  queued: number;
  /** p95 queue wait: submitted -> picked up by a worker. Windowed by the
   *  tenant's most recent 200 jobs, not by time, so it is sticky — a previous
   *  fairness-off round stays in the window until the job rows are reset. */
  p95_wait_seconds: number | null;
};

/** Worker Deployment routing (proof 2). `current_version` takes all new
 *  sessions; a ramp diverts `ramping_percentage` of them to
 *  `ramping_version`. Neither moves a session already in flight. */
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
  /** The worker build this session is pinned to. It does not change for the
   *  life of the session, which is what proof 2 demonstrates. */
  worker_version: string | null;
  /** Set while the agent is suspended waiting on a human. Resuming is a
   *  Temporal signal, so the session survives restarts while it waits. */
  pending_approval: {
    interrupt_id: string;
    tool: string;
    tool_input: Record<string, unknown>;
    policy: string;
  } | null;
  /** Temporal event history size for this session. Large payloads are not
   *  stored in it — they go to S3 via External Storage and history keeps a
   *  claim-check, which is what the two external_payload fields report. */
  history_size_bytes: number | null;
  external_payload_size_bytes: number | null;
  external_payload_count: number | null;
  /** The session's final answer, present only once it has COMPLETED. Not on
   *  the event stream: an agent whose manifest sets `output_model` returns a
   *  validated object on its last turn, so there are no tokens to stream. */
  result: string | null;
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
