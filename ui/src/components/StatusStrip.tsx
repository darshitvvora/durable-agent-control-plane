import type { FleetStatus } from "../types";

function Readout({ label, value, signal }: { label: string; value: string; signal?: boolean }) {
  return (
    <div className="flex items-baseline gap-2">
      <span className="font-display text-[13px] font-bold tracking-[0.2em] text-[#4a463d] uppercase">
        {label}
      </span>
      <span
        className={`font-mono text-[26px] leading-none font-semibold tabular-nums ${
          signal ? "text-alert" : "text-[#16140f]"
        }`}
      >
        {value}
      </span>
    </div>
  );
}

export function StatusStrip({ status }: { status: FleetStatus | null }) {
  const running = status?.jobs_by_status.Running ?? 0;
  const completed = status?.jobs_by_status.Completed ?? 0;
  const failed = status?.jobs_by_status.Failed ?? 0;

  return (
    <header className="raised flex shrink-0 flex-wrap items-center gap-x-8 gap-y-2 bg-chrome px-4 py-2">
      <h1 className="font-display text-[23px] font-bold tracking-[0.16em] text-[#16140f] uppercase">
        Durable Agent OS
      </h1>
      <div className="ml-auto flex flex-wrap items-center gap-x-7 gap-y-2">
        <Readout label="Workers" value={status ? String(status.workers) : "—"} />
        <Readout label="Running" value={String(running)} />
        <Readout label="Done" value={String(completed)} />
        {failed > 0 && <Readout label="Failed" value={String(failed)} signal />}
        <Readout
          label="Fairness"
          value={status ? (status.fairness_enabled ? "ON" : "OFF") : "—"}
          signal={status ? !status.fairness_enabled : false}
        />
      </div>
    </header>
  );
}
