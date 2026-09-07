import type { AgentPackage, Tenant } from "../types";
import { Panel, Well } from "./Panel";

/**
 * App store / package manager. Lists every *published* agent, and installs
 * them per tenant.
 *
 * The two concepts a reader needs:
 *
 * - **Publishing** is `dos agent publish <id>`, which writes an agent's
 *   manifest + SOP into DynamoDB. An agent is a directory under `agents/`
 *   (`manifest.yaml` + `procedure.sop.md`), never a code change — which is
 *   why this component has no per-agent branches in it.
 * - **Installing** grants one tenant access to one agent. Publishing makes an
 *   agent exist; installing decides who may run it.
 */

/**
 * Tier is how much of the agent this control plane runs itself:
 *   1 — SOP prompt only, no tools
 *   2 — SOP plus tools, each tool call dispatched as its own Temporal activity
 *   3 — someone else's agent loop, hosted on AgentCore Runtime; we invoke it
 *       once and get durability, fair queueing and retries around it, but no
 *       token stream or per-tool gating (that boundary is not ours)
 */
const TIER_LABEL: Record<string, string> = {
  "1": "No-code",
  "2": "Tools",
  "3": "Hosted",
};

export function AgentStore({
  agents,
  tenant,
  onInstall,
  onUninstall,
  busy,
}: {
  agents: AgentPackage[];
  tenant: Tenant | null;
  onInstall: (agentId: string) => void;
  onUninstall: (agentId: string) => void;
  busy: string | null;
}) {
  return (
    <Panel
      title="Agent Store"
      right={
        <span className="font-mono text-[13px] text-[#4a463d]">
          {tenant ? tenant.tenant_id : "—"}
        </span>
      }
      className="min-h-0"
    >
      <Well className="flex-1 space-y-2 overflow-auto">
        {agents.length === 0 && (
          <p className="font-mono text-[15px] text-ink-dim">
            No agents published. Run <span className="text-ink">dos agent publish &lt;id&gt;</span>.
          </p>
        )}
        {agents.map((agent) => {
          const installed = tenant?.installed_agent_ids.includes(agent.agent_id) ?? false;
          const hosted = agent.tier === "3";
          return (
            <article key={agent.agent_id} className="border-t border-[#2c2c22] pt-2 first:border-0">
              <div className="flex items-baseline justify-between gap-2">
                <h3 className="font-mono text-[20px] font-semibold">{agent.agent_id}</h3>
                <span className="font-mono text-[13px] text-ink-dim">v{agent.version}</span>
              </div>
              <div className="mt-1 flex flex-wrap items-center gap-2">
                <span
                  className={`px-2 py-[2px] font-display text-[13px] font-bold tracking-[0.14em] uppercase ${
                    hosted ? "bg-signal text-[#16140f]" : "bg-[#33332a] text-ink"
                  }`}
                >
                  {hosted ? "Hosted" : "Native"}
                </span>
                <span className="font-mono text-[14px] text-ink-dim">
                  tier {agent.tier} · {TIER_LABEL[agent.tier] ?? "—"}
                </span>
                {/* "gated" = the manifest declares an approval policy, so one
                    of this agent's tools pauses mid-session for a human above
                    a threshold. The session costs nothing while it waits. */}
                {agent.approval_policy && (
                  <span className="font-mono text-[14px] text-signal">gated</span>
                )}
              </div>
              <div className="mt-2 flex items-center gap-2">
                <button
                  className="btn"
                  disabled={!tenant || busy === agent.agent_id}
                  onClick={() =>
                    installed ? onUninstall(agent.agent_id) : onInstall(agent.agent_id)
                  }
                >
                  {installed ? "Remove" : "Install"}
                </button>
                {installed && (
                  <span className="font-mono text-[15px] text-signal">installed</span>
                )}
              </div>
            </article>
          );
        })}
      </Well>
    </Panel>
  );
}
