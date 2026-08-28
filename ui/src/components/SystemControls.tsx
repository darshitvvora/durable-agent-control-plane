import { useState } from "react";
import { api } from "../api";
import type { FleetStatus, Tenant } from "../types";
import { Panel, Well } from "./Panel";

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
  const [rampBuildId, setRampBuildId] = useState("");
  const [rampPercent, setRampPercent] = useState(25);
  const [busy, setBusy] = useState(false);
  const fairness = status?.fairness_enabled ?? true;
  const ramp = status?.ramp;

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

  const applyRamp = async () => {
    if (!rampBuildId) return;
    setBusy(true);
    try {
      await api.setRamp(rampBuildId, rampPercent);
      onChanged();
    } finally {
      setBusy(false);
    }
  };

  const clearRamp = async () => {
    setBusy(true);
    try {
      await api.clearRamp();
      onChanged();
    } finally {
      setBusy(false);
    }
  };

  const killWorker = async () => {
    setBusy(true);
    try {
      await api.killWorker();
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
              key={fairness ? "on" : "off"}
              className={`flip-flash font-mono text-[28px] leading-none font-semibold ${
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
              aria-label="flood count"
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

        <div className="border-t border-[#2c2c22] pt-3">
          <p className="font-display text-[13px] font-bold tracking-[0.18em] text-ink-dim uppercase">
            Worker deployment ramp
          </p>
          <p className="mt-1 font-mono text-[14px] text-ink-dim">
            current {ramp?.current_version || "—"} · ramping{" "}
            {ramp?.ramping_version ? `${ramp.ramping_version} @ ${ramp.ramping_percentage.toFixed(0)}%` : "none"}
          </p>
          <div className="mt-2 flex flex-wrap items-center gap-2">
            <input
              type="text"
              placeholder="target build id"
              value={rampBuildId}
              onChange={(e) => setRampBuildId(e.target.value)}
              className="sunken w-40 bg-well-hi px-2 py-2 font-mono text-[16px] text-ink"
            />
            <input
              type="number"
              aria-label="ramp percent"
              min={0}
              max={100}
              value={rampPercent}
              onChange={(e) => setRampPercent(Number(e.target.value))}
              className="sunken w-20 bg-well-hi px-2 py-2 font-mono text-[16px] text-ink"
            />
            <button className="btn" onClick={applyRamp} disabled={busy || !rampBuildId}>
              Ramp
            </button>
            <button className="btn" onClick={clearRamp} disabled={busy}>
              Clear
            </button>
          </div>
        </div>

        <div className="border-t border-[#2c2c22] pt-3">
          <p className="font-display text-[13px] font-bold tracking-[0.18em] text-ink-dim uppercase">
            Kill worker
          </p>
          <div className="mt-2 flex items-center gap-3">
            <button className="btn bg-alert text-white" onClick={killWorker} disabled={busy}>
              Arm at tool boundary
            </button>
          </div>
          <p className="mt-2 font-mono text-[14px] text-ink-dim">
            Crashes the worker right after its next payment call succeeds.
          </p>
        </div>
      </Well>
    </Panel>
  );
}
