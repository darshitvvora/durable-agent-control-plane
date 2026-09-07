import { useCallback, useEffect, useState } from "react";
import { api } from "./api";
import { AgentStore } from "./components/AgentStore";
import { ProcessMonitor } from "./components/ProcessMonitor";
import { SessionTerminal } from "./components/SessionTerminal";
import { StatusStrip } from "./components/StatusStrip";
import { SystemControls } from "./components/SystemControls";
import type { AgentPackage, FleetStatus, Job, Lane, Tenant } from "./types";

/** Fleet state is polled; a session's own output streams over SSE. Two seconds
 * is fast enough that a lane overtaking another reads as motion on stage,
 * without hammering Temporal's visibility API. */
const POLL_MS = 2000;

export default function App() {
  const [tenants, setTenants] = useState<Tenant[]>([]);
  const [agents, setAgents] = useState<AgentPackage[]>([]);
  const [lanes, setLanes] = useState<Lane[]>([]);
  const [status, setStatus] = useState<FleetStatus | null>(null);
  const [tenantId, setTenantId] = useState<string | null>(null);
  const [job, setJob] = useState<Job | null>(null);
  // Set by RunSession when a session is launched from the browser. While it is
  // set the terminal follows *that* session and auto-follow is off (E8.1 T0).
  const [pinnedJobId, setPinnedJobId] = useState<string | null>(null);
  const [busyAgent, setBusyAgent] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  const refresh = useCallback(async () => {
    try {
      const [nextTenants, nextAgents, nextLanes, nextStatus] = await Promise.all([
        api.tenants(),
        api.agents(),
        api.lanes(),
        api.status(),
      ]);
      setTenants(nextTenants);
      setAgents(nextAgents);
      setLanes(nextLanes);
      setStatus(nextStatus);
      setError(null);
      setTenantId((current) => current ?? nextTenants[0]?.tenant_id ?? null);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    }
  }, []);

  // Self-scheduling, not setInterval: setInterval fires on a fixed clock
  // regardless of whether the previous call finished, so a slow response
  // (metrics/status has been observed at 2.5s+ even against an idle stack)
  // causes requests to pile up faster than they drain — the whole API wedges
  // (docs/DECISIONS.md, E8.1 T5's API-side counterpart). Scheduling the next
  // call only after this one resolves means POLL_MS is the *gap* between
  // calls, not a fixed cadence that ignores how long the last one took.
  useEffect(() => {
    let cancelled = false;
    let timer: ReturnType<typeof setTimeout> | undefined;
    const loop = async () => {
      await refresh();
      if (cancelled) return;
      timer = setTimeout(loop, POLL_MS);
    };
    void loop();
    return () => {
      cancelled = true;
      if (timer) clearTimeout(timer);
    };
  }, [refresh]);

  // Follow the selected tenant's newest job — that is the session on screen —
  // but only while nothing is pinned. A session the operator started explicitly
  // must not be yanked away by a flood job arriving two seconds later: that
  // tears down a *live* EventSource mid-session (observed at readyState 1,
  // right after job_started), so `job_finished` never arrives and the pane
  // reads on stage as "the stream died". Root cause of E8.1 T0; see
  // docs/DECISIONS.md (2026-09-04).
  // Same non-overlapping self-scheduling as the poll above.
  useEffect(() => {
    if (!tenantId || pinnedJobId) return;
    let cancelled = false;
    let timer: ReturnType<typeof setTimeout> | undefined;
    const pick = async () => {
      try {
        const jobs = await api.jobs(tenantId);
        if (cancelled) return;
        const newest = jobs[0] ?? null;
        setJob((current) => (current?.job_id === newest?.job_id ? current : newest));
      } catch {
        /* the strip already surfaces API failure */
      }
    };
    const loop = async () => {
      await pick();
      if (cancelled) return;
      timer = setTimeout(loop, POLL_MS);
    };
    void loop();
    return () => {
      cancelled = true;
      if (timer) clearTimeout(timer);
    };
  }, [tenantId, pinnedJobId]);

  // The pinned session, resolved once. The Job row is written before
  // POST /api/jobs returns, but DynamoDB reads are eventually consistent, so a
  // first fetch can still 404 — retry rather than silently leave the pane on
  // the previous session.
  useEffect(() => {
    if (!pinnedJobId) return;
    let cancelled = false;
    let timer: ReturnType<typeof setTimeout> | undefined;
    const resolve = async () => {
      try {
        const pinned = await api.job(pinnedJobId);
        if (!cancelled) setJob(pinned);
      } catch {
        if (!cancelled) timer = setTimeout(resolve, 500);
      }
    };
    void resolve();
    return () => {
      cancelled = true;
      if (timer) clearTimeout(timer);
    };
  }, [pinnedJobId]);

  // Picking a tenant releases the pin — otherwise the first browser-started
  // session freezes the pane forever and clicking a lane does nothing.
  const selectTenant = useCallback((next: string) => {
    setPinnedJobId(null);
    setTenantId(next);
  }, []);

  const tenant = tenants.find((t) => t.tenant_id === tenantId) ?? null;

  const install = async (agentId: string) => {
    if (!tenantId) return;
    setBusyAgent(agentId);
    try {
      await api.install(tenantId, agentId);
      await refresh();
    } finally {
      setBusyAgent(null);
    }
  };

  const uninstall = async (agentId: string) => {
    if (!tenantId) return;
    setBusyAgent(agentId);
    try {
      await api.uninstall(tenantId, agentId);
      await refresh();
    } finally {
      setBusyAgent(null);
    }
  };

  return (
    <div className="flex h-full flex-col gap-2 bg-chrome p-2">
      <StatusStrip status={status} />

      {error && (
        <div className="raised shrink-0 bg-alert px-4 py-2 font-mono text-[15px] text-white">
          API unreachable: {error}. Start it with <span className="font-semibold">make api</span>.
        </div>
      )}

      <main className="grid min-h-0 flex-1 grid-cols-1 gap-2 lg:grid-cols-[minmax(300px,1fr)_minmax(0,2fr)]">
        <div className="grid min-h-0 grid-rows-2 gap-2">
          <AgentStore
            agents={agents}
            tenant={tenant}
            onInstall={install}
            onUninstall={uninstall}
            busy={busyAgent}
          />
          <SystemControls
            status={status}
            tenants={tenants}
            tenant={tenantId}
            onTenantChange={selectTenant}
            onChanged={refresh}
          />
        </div>
        <div className="grid min-h-0 grid-rows-2 gap-2">
          <ProcessMonitor lanes={lanes} selected={tenantId} onSelect={selectTenant} />
          <SessionTerminal
            job={job}
            agents={agents}
            tenantId={tenantId}
            onStarted={setPinnedJobId}
          />
        </div>
      </main>
    </div>
  );
}
