import { useState } from "react";
import { api } from "../api";
import type { FleetStatus, Tenant } from "../types";
import { Panel, Well } from "./Panel";

/** Only controls with a real backend appear here. Version ramp (E6.1) and kill
 * worker (E6.2) are deliberately absent rather than shown dead — nothing on a
 * conference screen should be a prop (CLAUDE.md §7). */
export function SystemControls({
  status,
  tenants,
  tenant,
  onTenantChange,
  onChanged,
}: {
  status: FleetStatus | null;
  tenants: Tenant[];
  tenant: string | null;
  onTenantChange: (tenantId: string) => void;
  onChanged: () => void;
}) {
  const [count, setCount] = useState(20);
  const [busy, setBusy] = useState(false);
  const fairness = status?.fairness_enabled ?? true;

  const toggleFairness = async () => {
    setBusy(true);
    try {
      await api.setFairness(!fairness);
      onChanged();
    } finally {
      setBusy(false);
    }
  };

  const flood = async () => {
    if (!tenant) return;
    setBusy(true);
    try {
      await api.flood(tenant, count);
      onChanged();
    } finally {
      setBusy(false);
    }
  };

  return (
    <Panel title="System Controls" className="min-h-0">
      <Well className="flex-1 space-y-4 overflow-auto">
        <div>
          <p className="font-display text-[13px] font-bold tracking-[0.18em] text-ink-dim uppercase">
            Task queue fairness
          </p>
          <div className="mt-2 flex items-center gap-3">
            <button className="btn" onClick={toggleFairness} disabled={busy}>
              {fairness ? "Turn off" : "Turn on"}
            </button>
            <span
              className={`font-mono text-[28px] leading-none font-semibold ${
                fairness ? "text-ink" : "text-alert"
              }`}
            >
              {fairness ? "ON" : "OFF"}
            </span>
          </div>
        </div>

        <div className="border-t border-[#2c2c22] pt-3">
          <p className="font-display text-[13px] font-bold tracking-[0.18em] text-ink-dim uppercase">
            Flood a tenant
          </p>
          <div className="mt-2 flex flex-wrap items-center gap-2">
            <select
              className="sunken bg-well-hi px-2 py-2 font-mono text-[16px] text-ink"
              value={tenant ?? ""}
              onChange={(e) => onTenantChange(e.target.value)}
            >
              {tenants.map((t) => (
                <option key={t.tenant_id} value={t.tenant_id}>
                  {t.tenant_id}
                </option>
              ))}
            </select>
            <input
              type="number"
              min={1}
              max={500}
              value={count}
              onChange={(e) => setCount(Number(e.target.value))}
              className="sunken w-24 bg-well-hi px-2 py-2 font-mono text-[16px] text-ink"
            />
            <button className="btn" onClick={flood} disabled={busy || !tenant}>
              Run
            </button>
          </div>
          <p className="mt-2 font-mono text-[14px] text-ink-dim">
            Every job is a real Bedrock call.
          </p>
        </div>
      </Well>
    </Panel>
  );
}
