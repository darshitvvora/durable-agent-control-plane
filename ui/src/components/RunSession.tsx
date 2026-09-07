import { useState } from "react";
import { api } from "../api";
import type { AgentPackage } from "../types";

/** Launch one agent session, as a compact single-line row inside the Session
 * panel — where its output appears. Without this the UI can only start
 * floods, whose agent is tier 1 and toolless — i.e. the only browser-
 * launchable agent touches no AgentCore services at all. Beats 0 and 2 need
 * this.
 *
 * Deliberately presentational, not its own Panel: the Agent Store is a list
 * that must be read whole, so it cannot spare vertical space, but the
 * Session terminal is a scrolling log that can (CLAUDE.md §7, fix round 1). */
export function RunSession({
  agents,
  tenantId,
  onStarted,
}: {
  agents: AgentPackage[];
  tenantId: string | null;
  onStarted: (jobId: string) => void;
}) {
  const [agentId, setAgentId] = useState<string>("");
  const [prompt, setPrompt] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const selected = agentId || agents[0]?.agent_id || "";

  const run = async () => {
    if (!tenantId || !selected || !prompt.trim()) return;
    setBusy(true);
    setError(null);
    try {
      const { job_id } = await api.runSession(selected, tenantId, prompt.trim());
      onStarted(job_id);
      setPrompt("");
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="mb-2 flex shrink-0 flex-wrap items-center gap-2">
      <select
        className="btn px-2 py-1 font-mono text-[13px]"
        value={selected}
        onChange={(e) => setAgentId(e.target.value)}
      >
        {agents.map((a) => (
          <option key={a.agent_id} value={a.agent_id}>
            {a.name} · tier {a.tier}
          </option>
        ))}
      </select>
      <input
        type="text"
        className="raised min-w-[220px] flex-1 bg-[#1b1a16] px-2 py-1 font-mono text-[13px] text-ink"
        placeholder="Prompt for this session"
        value={prompt}
        onChange={(e) => setPrompt(e.target.value)}
      />
      <button
        className="btn px-3 py-1 text-[13px]"
        disabled={busy || !tenantId || !prompt.trim()}
        onClick={run}
      >
        {busy ? "Starting..." : "Run"}
      </button>
      {error && <p className="font-mono text-[13px] text-alert">{error}</p>}
    </div>
  );
}
