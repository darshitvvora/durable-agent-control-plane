import { useEffect, useRef, useState } from "react";
import { api, eventsUrl } from "../api";
import type { AgentPackage, Job, SessionState } from "../types";
import { Panel, Well } from "./Panel";
import { RunSession } from "./RunSession";

type Entry =
  | { kind: "text"; text: string }
  | { kind: "reasoning"; text: string }
  | { kind: "tool"; tool: string }
  | { kind: "guardrail"; tool: string; blocked: boolean; reason: string | null }
  | { kind: "note"; text: string };

/** Live tty for one session: tokens as they stream, tool calls as they fire. */
export function SessionTerminal({
  job,
  agents,
  tenantId,
  onStarted,
}: {
  job: Job | null;
  agents: AgentPackage[];
  tenantId: string | null;
  onStarted: (jobId: string) => void;
}) {
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

    const onWorkflow = (name: string, handler: (payload: Record<string, unknown>) => void) =>
      source.addEventListener(name, (e) =>
        handler(JSON.parse((e as MessageEvent).data).payload ?? {}),
      );

    source.onopen = () => !cancelled && setLive(true);

    // EventSource retries on its own whenever the server closes the response.
    // That is what you want while a session is still running, and exactly what
    // you must not do once it is over: a finished workflow's stream is gone, so
    // every retry reconnects, gets an immediate close, and retries again —
    // forever, holding a connection slot per cycle until the page starves and
    // *later* sessions stop streaming too. Closing on `job_finished` alone does
    // not cover it, because the pane can attach to a job that already finished
    // (a fast session can complete inside the 2s job-poll interval, and an
    // operator can select any past session): in that case no `job_finished`
    // ever arrives and there is nothing to close on. So settle it against real
    // execution status instead — the polled-state endpoint E4.2 T3 already
    // added for the no-stream case.
    source.onerror = () => {
      if (cancelled) return;
      setLive(false);
      void api
        .jobState(job.job_id)
        .then((s) => {
          if (cancelled) return;
          setState(s);
          if (s.status !== "RUNNING") source.close();
        })
        .catch(() => {
          /* transient: leave the retry alone, the strip surfaces API failure */
        });
    };

    onFlat("token", (d) => append({ kind: "text", text: d.text }));
    onFlat("reasoning", (d) => append({ kind: "reasoning", text: d.text }));
    onFlat("tool_call", (d) => append({ kind: "tool", tool: d.tool }));
    onWorkflow("job_started", (p) =>
      append({ kind: "note", text: `session started — ${String(p.agent_id ?? "")}` }),
    );
    // Bedrock Guardrails verdict on a proposed consequential tool call (E7.1 T3),
    // published by the workflow onto the same job_events topic.
    onWorkflow("guardrail", (p) =>
      append({
        kind: "guardrail",
        tool: String(p.tool ?? "tool"),
        blocked: Boolean(p.blocked),
        reason: p.reason == null ? null : String(p.reason),
      }),
    );
    onWorkflow("approval_pending", (p) => {
      append({ kind: "note", text: `awaiting approval — ${String(p.tool ?? "tool")}` });
      void api.jobState(job.job_id).then((s) => !cancelled && setState(s));
    });
    onWorkflow("approval_resumed", (p) => {
      append({ kind: "note", text: `reviewer said: ${String(p.decision)}` });
      setState((prev) => (prev ? { ...prev, pending_approval: null } : prev));
    });
    onWorkflow("job_finished", (p) => {
      append({ kind: "note", text: `session finished — ${String(p.stop_reason ?? "")}` });
      setLive(false);
      // Close, do not just stop reading. A finished workflow's stream is gone,
      // but EventSource reconnects on its own whenever the server closes the
      // response — so leaving it open starts an endless reconnect loop that
      // holds a browser connection slot per cycle. A few finished sessions and
      // the per-origin limit is reached, at which point the *next* session's
      // stream silently never connects: the pane shows the new job id, polls
      // its state, and sits at "waiting for output" forever. Which is exactly
      // what a presenter running several sessions in a row would hit.
      source.close();
      // Claim-check numbers (E7.2 T4) only settle once the run has closed.
      void api.jobState(job.job_id).then((s) => !cancelled && setState(s));
    });

    // The polled fallback: if the stream never opens, the pane still shows real
    // state rather than going blank (E4.2 T3).
    void api.jobState(job.job_id).then((s) => !cancelled && setState(s));

    return () => {
      cancelled = true;
      source.close();
    };
    // Keyed on the job *id*, not the object. Two fetches of the same session
    // (the tenant poll and the pinned-session fetch) return equal-but-distinct
    // objects, and keying on identity made the second one tear down a stream
    // that was already live on that very job — the same failure this pin exists
    // to prevent, just self-inflicted (E8.1 T0).
  }, [job?.job_id]);

  useEffect(() => {
    wellRef.current?.scrollTo({ top: wellRef.current.scrollHeight });
  }, [entries]);

  const pending = state?.pending_approval ?? null;
  // Storage pane (E7.2 T4) — only when this session actually offloaded a
  // payload via External Storage; most sessions never trigger it.
  const storage =
    state?.external_payload_count && state.external_payload_count > 0
      ? {
          externalKB: Math.round(state.external_payload_size_bytes! / 1024),
          historyKB: Math.round((state.history_size_bytes ?? 0) / 1024),
          count: state.external_payload_count,
        }
      : null;

  return (
    <Panel
      title="Session"
      right={
        <span className="flex items-center gap-3 font-mono text-[14px] text-[#4a463d]">
          {state?.worker_version && (
            <span
              key={state.worker_version}
              className="badge-in bg-[#33332a] px-2 py-[2px] text-ink"
            >
              {state.worker_version}
            </span>
          )}
          <span className={live ? "text-[#1d6b2f]" : ""}>{live ? "● live" : "○ idle"}</span>
        </span>
      }
      className="min-h-0"
    >
      <RunSession agents={agents} tenantId={tenantId} onStarted={onStarted} />
      <Well className="flex-1 overflow-auto font-mono text-[18px] leading-[1.5]" >
        {/* Which session this transcript belongs to. On stage it is the job id
            already printed in the empty state; in `make e2e` it is the only way
            to assert that the pane held *this* session to completion rather
            than being swapped onto a flood job mid-stream (E8.1 T0). */}
        <div ref={wellRef} data-job-id={job?.job_id} className="h-full overflow-auto">
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
            // A block is the one thing here that stops real work, so it reads
            // as an alert bar; a pass stays quiet, so the pane does not cry
            // wolf when the guardrail simply agreed (CLAUDE.md §7).
            if (entry.kind === "guardrail")
              return entry.blocked ? (
                <div key={i} className="my-1 bg-alert px-2 py-[2px] text-ink">
                  ✕ guardrail blocked {entry.tool}
                  {entry.reason ? ` — ${entry.reason}` : ""}
                </div>
              ) : (
                <div key={i} className="my-1 text-ink-dim">
                  ✓ guardrail passed — {entry.tool}
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

      {storage && (
        <p className="mt-2 shrink-0 font-mono text-[13px] text-ink-dim">
          storage — {storage.count} payload{storage.count === 1 ? "" : "s"} offloaded to S3 (
          {storage.externalKB} KB) · event history {storage.historyKB} KB
        </p>
      )}

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
