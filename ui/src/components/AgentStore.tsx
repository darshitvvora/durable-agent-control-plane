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
            // One row per agent, not three. Stacked rows ran ~105px each, so
            // the fifth agent fell below the fold at 1920x1080 and a stage
            // audience cannot scroll (CLAUDE.md §7). The hosted-lane beat
            // registers an agent live and the room has to *see* it appear, so
            // the list must hold six. `flex-wrap` degrades to two lines on a
            // narrow window rather than truncating an agent id.
            <article
              key={agent.agent_id}
              className="flex flex-wrap items-center gap-x-2 gap-y-1 border-t border-[#2c2c22] pt-2 first:border-0"
            >
              <h3 className="font-mono text-[20px] font-semibold">{agent.agent_id}</h3>
              <span className="font-mono text-[13px] text-ink-dim">v{agent.version}</span>
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
              {/* "gated" = the manifest declares an approval policy, so one of
                  this agent's tools pauses mid-session for a human above a
                  threshold. The session costs nothing while it waits. */}
              {agent.approval_policy && (
                <span className="font-mono text-[14px] text-signal">gated</span>
              )}
              {/* The button is the install state — `Remove` means installed,
                  `Install` means not. A separate "installed" label alongside it
                  said the same thing twice and cost 88px of the 592px row,
                  which is what forced these cards to wrap to two lines. */}
              <button
                className="btn ml-auto"
                disabled={!tenant || busy === agent.agent_id}
                onClick={() => (installed ? onUninstall(agent.agent_id) : onInstall(agent.agent_id))}
              >
                {installed ? "Remove" : "Install"}
              </button>
            </article>
          );
        })}
      </Well>
    </Panel>
  );
}
