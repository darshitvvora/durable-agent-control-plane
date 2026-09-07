import { useLayoutEffect, useRef } from "react";
import type { Lane } from "../types";
import { Panel, Well } from "./Panel";

/**
 * `htop` for tenants — one lane per tenant, busiest first.
 *
 * Every column is a real call, not a sample: `running` is Temporal's own
 * visibility store (count_workflows grouped by the TenantId search
 * attribute), `queued` is the job index in DynamoDB, and `p95` is measured
 * queue wait — the gap between a job being submitted and a worker picking it
 * up. p95 is the number proof 1 turns on: with fairness on, a flooding
 * tenant's own p95 climbs while everyone else's holds flat.
 *
 * `w1.0` / `w3.0` is the tenant's fairness weight, straight from the
 * registry: weight 3 gets roughly three times the share of weight 1 when
 * both are contending.
 */

/** FLIP: when a lane re-sorts (proof 1's "lane overtaking"), ease the row to
 * its new position instead of letting it snap there silently. */
function useLaneFlip(order: string[]) {
  const bodyRef = useRef<HTMLTableSectionElement>(null);
  const prevTops = useRef<Map<string, number>>(new Map());
  const orderKey = order.join(",");

  useLayoutEffect(() => {
    const rows = bodyRef.current?.querySelectorAll<HTMLElement>("[data-row-key]");
    rows?.forEach((row) => {
      const key = row.dataset.rowKey!;
      const prevTop = prevTops.current.get(key);
      const nextTop = row.getBoundingClientRect().top;
      if (prevTop !== undefined) {
        const delta = prevTop - nextTop;
        if (delta) {
          row.style.transition = "none";
          row.style.transform = `translateY(${delta}px)`;
          requestAnimationFrame(() => {
            row.style.transition = "";
            row.style.transform = "";
          });
        }
      }
      prevTops.current.set(key, nextTop);
    });
  }, [orderKey]);

  return bodyRef;
}

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
  const bodyRef = useLaneFlip(lanes.map((l) => l.tenant_id));

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
          <tbody ref={bodyRef}>
            {lanes.map((lane) => {
              const load = (lane.running ?? 0) + lane.queued;
              const isSelected = lane.tenant_id === selected;
              return (
                <tr
                  key={lane.tenant_id}
                  data-row-key={lane.tenant_id}
                  onClick={() => onSelect(lane.tenant_id)}
                  className={`lane-row cursor-pointer border-t border-[#2c2c22] ${
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
