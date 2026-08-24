import { useEffect, useRef, useState } from "react";
import { api, eventsUrl } from "../api";
import type { Job, SessionState } from "../types";
import { Panel, Well } from "./Panel";

type Entry =
  | { kind: "text"; text: string }
  | { kind: "reasoning"; text: string }
  | { kind: "tool"; tool: string }
  | { kind: "note"; text: string };

/** Live tty for one session: tokens as they stream, tool calls as they fire. */
export function SessionTerminal({ job }: { job: Job | null }) {
  const [entries, setEntries] = useState<Entry[]>([]);
  const [state, setState] = useState<SessionState | null>(null);
  const [live, setLive] = useState(false);
  const wellRef = useRef<HTMLDivElement>(null);

  // Reconnect the stream whenever the selected session changes.
  useEffect(() => {
    setEntries([]);
    setState(null);
    setLive(false);
    if (!job) return;

    let cancelled = false;
    const source = new EventSource(eventsUrl(job.job_id));

    const append = (entry: Entry) =>
      setEntries((prev) => {
        // Tokens coalesce into the trailing text run so the terminal reads as
        // prose rather than one line per chunk.
        const last = prev[prev.length - 1];
        if (entry.kind === "text" && last?.kind === "text") {
          return [...prev.slice(0, -1), { kind: "text", text: last.text + entry.text }];
        }
        if (entry.kind === "reasoning" && last?.kind === "reasoning") {
          return [...prev.slice(0, -1), { kind: "reasoning", text: last.text + entry.text }];
        }
        return [...prev, entry];
      });

    // Two topics reach this stream with different shapes. Events the workflow
    // publishes (job_started, approval_*, job_finished) are UIEvents, so their
    // detail sits under `payload`. Events translated from the model stream
    // (token, reasoning, tool_call) are already flat.
    const onFlat = (name: string, handler: (data: Record<string, string>) => void) =>
      source.addEventListener(name, (e) => handler(JSON.parse((e as MessageEvent).data)));

    const onWorkflow = (name: string, handler: (payload: Record<string, string>) => void) =>
      source.addEventListener(name, (e) =>
        handler(JSON.parse((e as MessageEvent).data).payload ?? {}),
      );

    source.onopen = () => !cancelled && setLive(true);
    source.onerror = () => !cancelled && setLive(false);

    onFlat("token", (d) => append({ kind: "text", text: d.text }));
    onFlat("reasoning", (d) => append({ kind: "reasoning", text: d.text }));
    onFlat("tool_call", (d) => append({ kind: "tool", tool: d.tool }));
    onWorkflow("job_started", (p) =>
      append({ kind: "note", text: `session started — ${p.agent_id ?? ""}` }),
    );
    onWorkflow("approval_pending", (p) => {
      append({ kind: "note", text: `awaiting approval — ${p.tool ?? "tool"}` });
      void api.jobState(job.job_id).then((s) => !cancelled && setState(s));
    });
    onWorkflow("approval_resumed", (p) => {
      append({ kind: "note", text: `reviewer said: ${p.decision}` });
      setState((prev) => (prev ? { ...prev, pending_approval: null } : prev));
    });
    onWorkflow("job_finished", (p) => {
      append({ kind: "note", text: `session finished — ${p.stop_reason ?? ""}` });
      setLive(false);
    });

    // The polled fallback: if the stream never opens, the pane still shows real
    // state rather than going blank (E4.2 T3).
    void api.jobState(job.job_id).then((s) => !cancelled && setState(s));

    return () => {
      cancelled = true;
      source.close();
    };
  }, [job]);

  useEffect(() => {
    wellRef.current?.scrollTo({ top: wellRef.current.scrollHeight });
  }, [entries]);

  const pending = state?.pending_approval ?? null;

  return (
    <Panel
      title="Session"
      right={
        <span className="flex items-center gap-3 font-mono text-[14px] text-[#4a463d]">
          {state?.worker_version && (
            <span className="bg-[#33332a] px-2 py-[2px] text-ink">{state.worker_version}</span>
          )}
          <span className={live ? "text-[#1d6b2f]" : ""}>{live ? "● live" : "○ idle"}</span>
        </span>
      }
      className="min-h-0"
    >
      <Well className="flex-1 overflow-auto font-mono text-[18px] leading-[1.5]" >
        <div ref={wellRef} className="h-full overflow-auto">
          {!job && <p className="text-ink-dim">Select a tenant with a running session.</p>}
          {job && entries.length === 0 && (
            <p className="text-ink-dim">
              {job.job_id} — waiting for output{state ? ` (${state.status.toLowerCase()})` : ""}
            </p>
          )}
          {entries.map((entry, i) => {
            if (entry.kind === "tool")
              return (
                <div key={i} className="my-1 text-signal">
                  → {entry.tool}
                </div>
              );
            if (entry.kind === "note")
              return (
                <div key={i} className="my-1 text-ink-dim">
                  ── {entry.text}
                </div>
              );
            if (entry.kind === "reasoning")
              return (
                <div key={i} className="my-1 border-l-2 border-[#33332a] pl-2 text-ink-dim italic">
                  {entry.text}
                </div>
              );
            return (
              <span key={i} className="whitespace-pre-wrap">
                {entry.text}
              </span>
            );
          })}
          {live && <span className="caret text-signal">▊</span>}
        </div>
      </Well>

      {pending && (
        <div className="raised mt-2 shrink-0 bg-signal p-3">
          <p className="font-display text-[16px] font-bold tracking-[0.1em] text-[#16140f] uppercase">
            Approval required — {pending.tool}
          </p>
          <p className="mt-1 font-mono text-[15px] text-[#3a2f13]">{pending.policy}</p>
          <div className="mt-2 flex gap-2">
            <button
              className="btn"
              onClick={() =>
                job && void api.approve(job.job_id, pending.interrupt_id, "approve")
              }
            >
              Approve
            </button>
            <button
              className="btn"
              onClick={() =>
                job &&
                void api.approve(job.job_id, pending.interrupt_id, "denied by reviewer on stage")
              }
            >
              Deny
            </button>
          </div>
        </div>
      )}
    </Panel>
  );
}
