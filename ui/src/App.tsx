import { useCallback, useEffect, useState } from "react";
import { api } from "./api";
import { AgentStore } from "./components/AgentStore";
import { ProcessMonitor } from "./components/ProcessMonitor";
import { RunSession } from "./components/RunSession";
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
  // Set by RunSession when a session is launched from the browser. Not wired
  // into the job-selection effect below yet — that is Task 4's job, which
  // makes the terminal follow this session instead of the tenant's newest.
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

  useEffect(() => {
    void refresh();
    const timer = setInterval(() => void refresh(), POLL_MS);
    return () => clearInterval(timer);
  }, [refresh]);

  // Follow the selected tenant's newest job — that is the session on screen.
  useEffect(() => {
    if (!tenantId) return;
    let cancelled = false;
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
    void pick();
    const timer = setInterval(() => void pick(), POLL_MS);
    return () => {
      cancelled = true;
      clearInterval(timer);
    };
  }, [tenantId]);

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
        <div className="grid min-h-0 grid-rows-[1fr_1fr_auto] gap-2">
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
            onTenantChange={setTenantId}
            onChanged={refresh}
          />
          <RunSession agents={agents} tenantId={tenantId} onStarted={setPinnedJobId} />
        </div>
        <div className="grid min-h-0 grid-rows-2 gap-2">
          <ProcessMonitor lanes={lanes} selected={tenantId} onSelect={setTenantId} />
          <SessionTerminal job={job} />
        </div>
      </main>
    </div>
  );
}
