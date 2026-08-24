import type { Lane } from "../types";
import { Panel, Well } from "./Panel";

/** Blocky character-cell bar — an htop lane, not a smooth progress bar. */
function LoadBar({ filled, total }: { filled: number; total: number }) {
  const cells = 18;
  const lit = total <= 0 ? 0 : Math.min(cells, Math.round((filled / total) * cells));
  return (
    <span className="font-mono text-[22px] leading-none tracking-[-0.05em] whitespace-nowrap">
      <span className="text-signal">{"█".repeat(lit)}</span>
      <span className="text-[#33332a]">{"█".repeat(cells - lit)}</span>
    </span>
  );
}

function Wait({ seconds }: { seconds: number | null }) {
  if (seconds === null) return <span className="text-ink-dim">—</span>;
  // Wait time is the number proof 1 lives or dies on, so it is the largest
  // thing in the lane, and it turns red only once a lane is visibly starved.
  const starved = seconds >= 8;
  return (
    <span
      className={`font-mono text-[30px] leading-none font-semibold tabular-nums ${
        starved ? "text-alert" : "text-ink"
      }`}
    >
      {seconds.toFixed(1)}s
    </span>
  );
}

export function ProcessMonitor({
  lanes,
  selected,
  onSelect,
}: {
  lanes: Lane[];
  selected: string | null;
  onSelect: (tenantId: string) => void;
}) {
  const busiest = Math.max(1, ...lanes.map((l) => (l.running ?? 0) + l.queued));

  return (
    <Panel
      title="Process Monitor"
      right={
        <span className="font-mono text-[13px] text-[#4a463d]">
          {lanes.length} tenants · p95 wait
        </span>
      }
      className="min-h-0"
    >
      <Well className="flex-1 overflow-auto">
        <table className="w-full border-collapse">
          <thead>
            <tr className="text-left font-display text-[13px] tracking-[0.18em] text-ink-dim uppercase">
              <th className="pb-2 font-bold">Tenant</th>
              <th className="pb-2 font-bold">Tier</th>
              <th className="pb-2 font-bold">Load</th>
              <th className="pb-2 text-right font-bold">Run</th>
              <th className="pb-2 text-right font-bold">Queue</th>
              <th className="pb-2 text-right font-bold">p95</th>
            </tr>
          </thead>
          <tbody>
            {lanes.map((lane) => {
              const load = (lane.running ?? 0) + lane.queued;
              const isSelected = lane.tenant_id === selected;
              return (
                <tr
                  key={lane.tenant_id}
                  onClick={() => onSelect(lane.tenant_id)}
                  className={`cursor-pointer border-t border-[#2c2c22] ${
                    isSelected ? "bg-well-hi" : ""
                  }`}
                >
                  <td className="py-2 pr-4">
                    <span className="font-mono text-[21px] font-semibold">{lane.tenant_id}</span>
                  </td>
                  <td className="py-2 pr-4">
                    <span className="font-mono text-[15px] text-ink-dim">
                      w{lane.fairness_weight.toFixed(1)}
                    </span>
                  </td>
                  <td className="py-2 pr-4">
                    <LoadBar filled={load} total={busiest} />
                  </td>
                  <td className="py-2 pr-3 text-right font-mono text-[21px] tabular-nums">
                    {lane.running === null ? <span className="text-ink-dim">?</span> : lane.running}
                  </td>
                  <td className="py-2 pr-4 text-right font-mono text-[21px] tabular-nums">
                    {lane.queued}
                  </td>
                  <td className="py-2 text-right">
                    <Wait seconds={lane.p95_wait_seconds} />
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
        {lanes.some((l) => l.running === null) && (
          <p className="mt-3 font-mono text-[13px] text-ink-dim">
            Run count needs the TenantId search attribute on the namespace — see docs/AWS_SETUP.md.
          </p>
        )}
      </Well>
    </Panel>
  );
}
