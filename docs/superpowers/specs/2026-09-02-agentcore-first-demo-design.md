# AgentCore-first demo — design

**Date:** 2026-09-02
**Status:** approved, not yet implemented
**Supersedes:** the implicit three-proof-only run of show described in `docs/BACKLOG.md` E8.1 T2

---

## Why this exists

The three proofs (fairness, safe version routing, kill-and-resume) are stated as **Temporal** properties. An AWS audience hears "fair queueing, version pinning, crash recovery" and files it under infrastructure. Nothing in that list contains the word *agent*, and nothing shows AgentCore at all — at re:Invent, that is the half the room came for.

The fix is **not** to cut a proof. Each proof is already an AgentCore-durability story narrated as a Temporal story:

| Narrated today | Narrated agent-first |
|---|---|
| Kill the worker, no duplicate payment | Your agent called a real payment API through a tool. The host died between the money moving and the system knowing. It did not pay twice. |
| Fairness protects other tenants | Fifty agent sessions from one tenant, each doing Gateway lookups and Memory reads, don't starve anyone else's agents. |
| In-flight sessions stay pinned to v1 | You redeployed your agent while twenty sessions were mid-reasoning. None of them lost their reasoning. |

Same demonstrations, different framing, zero extra minutes.

What is genuinely missing is a **beat 0** that shows the architecture before the proofs stress it: one session where Memory recall, a Gateway/MCP tool call, a Swarm handoff, a guardrail verdict and a human approval each happen live — and each one lands in Temporal's Event History as a durable, retryable event. That makes "outer durable harness" literal instead of asserted.

**Revised thesis line for the stage:**

> Strands runs the turn. AgentCore gives the agent hands. Temporal makes every reach survivable.

This extends CLAUDE.md §1's line rather than replacing it; the existing one has no AgentCore in it.

---

## Decisions taken

1. **Beat 0 plus reframing, nothing cut.** All three proofs stay and remain the acceptance test (CLAUDE.md §1).
2. **Both native agents appear:** `invoice-exception` for the main arc, `dispute-resolution` for Code Interpreter and guardrails.
3. **12-minute demo**, expanded from 8; framing slides are trimmed instead.
4. **The hosted lane gets a full extensibility beat**, including live registration by ARN.
5. **The AWS console appears**, sequenced rather than tiled — a three-way split is not legible at ten metres (CLAUDE.md §7).
6. **Beat 0's recalled memory is real, not seeded.** Demo setup runs two real `invoice-exception` sessions beforehand so recall surfaces genuine prior decisions. No fabricated rows (CLAUDE.md §7's no-fake-data rule).
7. **Reset purges tenant memory** via the `DeleteEvent` API, then re-seeds those two sessions.
8. **Beat 3 registers the ARN from the terminal, not a UI form.** Less to build, and more credible to a developer audience than a button.

---

## Run of show

**Cold open — before beat 0.** Start the initech flood while still on the intro slide. The Process Monitor is alive from the first second the UI is on screen, and proof 1 later becomes *"this has been true the whole time — watch what happens when I turn it off."* This also removes the need for a dedicated flood-submission beat.

**Establishing shot — AWS console · 0:20.** Bedrock AgentCore: Gateway `READY` with its `vendor-directory` target, Memory `ACTIVE`, Runtime `READY`. Said once, never re-litigated. Avoid CloudWatch metrics entirely — a 1–5 minute lag means nothing will have appeared by the time it is pointed at.

### Beat 0 — "An agent with hands" · 2:30

One `invoice-exception` session, launched **from the UI**. Screen split two ways: Session Terminal left, Temporal Event History right. As each thing streams on the left, point at the event it became on the right.

| Session terminal | Event History | Service demonstrated |
|---|---|---|
| memory recall | `recall_tenant_memory` | AgentCore Memory |
| token stream | `invoke_model_streaming` | Amazon Bedrock |
| `→ lookup_vendor_risk` | `vendor-directory-call-tool` | AgentCore Gateway (MCP) |
| fraud handoff | `investigate_fraud_swarm` | Strands Swarm (multi-agent) |
| approval pause | workflow signal | human in the loop |
| `→ issue_payment` | idempotent activity | real external side effect |
| structured decision | workflow result | typed output |

Closing line: *"Every reach outside the model is a durable event."*

**Console moment · 0:20** — Memory console showing the event this session just wrote. The only genuinely live console view; it proves memory is real AWS state rather than a local cache, and it closes the loop with the recall that opened the beat.

> **Unverified:** whether the Memory console renders individual events. The `ListEvents` API returns them; console rendering has not been confirmed. If it does not, this moment does not exist and the establishing shot is the only console view. **Check before scripting narration around it.**

### Beat 1 — Crash · 1:30 · proof 3

Same agent, same invoice, no context reset. Arm the kill, run, the payment reaches the provider, the worker dies before Temporal records completion, restart, the session resumes mid-turn, the counter still reads 1.

Line: *"AgentCore did the work. Temporal made sure it happened exactly once."*

Verified working manually on 2026-09-02: `workflow_execution_started` = 1, `issue_payment` at `attempt=2` while every other activity is `attempt=1`, and the confirmation id returned to the agent is the one recorded *before* the crash.

### Beat 2 — Sandbox and guardrail · 1:30

`dispute-resolution`: AgentCore Code Interpreter runs `analyze_dispute_risk`, then a Bedrock Guardrail blocks a filing.

Line: *"The sandbox is an activity. The guardrail is an activity. Both retryable, both in the audit log."*

> **Risk:** the guardrail block took five attempts to trigger reliably (`docs/DECISIONS.md`, 2026-09-01) because the denied topic overlaps the model's own refusal boundary. The exact prompt that reliably triggers it must be fixed in the script and rehearsed, not improvised.

### Beat 3 — Extensibility, the hosted lane · 1:45

Register VendorCheck **by ARN, live, from the terminal** (`dos agent register-hosted --runtime-arn ...`), watch it appear in the Agent Store, install it to a tenant, run it. Event History shows `invoke_hosted_agent` and **zero** `invoke_model*` — it ran on AgentCore Runtime, yet still received fair queueing, retries, memory and full Event History.

Includes a **console moment · 0:15** — the Runtime console showing the VendorCheck runtime and the ARN being pasted.

Line: *"A new agent is a manifest or an ARN. Never a code change."*

### Beat 4 — Fairness · 2:30 · proof 1

Point at the flood that has been running since the cold open. Show per-tenant p95. Toggle fairness off, watch protected tenants collapse toward the flooder. Toggle back on.

> **Blocked on E8.1 T5.** Under flood, part of the measured wait is worker event-loop starvation rather than queue position, so the numbers are not currently trustworthy. See "Prerequisites".

### Beat 5 — Versioning · 2:00 · proof 2

Sessions in flight, deploy v2, ramp. In-flight sessions stay pinned; new sessions start on v2 and return a structured `InvoiceDecision` where v1 returned prose.

### Close · 0:30

**Total ≈ 12:55** — 0:20 establishing shot, 2:30 + 0:20 beat 0, 1:30 beat 1, 1:30 beat 2, 1:45 beat 3 (console moment included), 2:30 beat 4, 2:00 beat 5, 0:30 close. No slack; the recorded fallback (E8.1 T3) covers a failure, not an overrun. If the slot is firm at 12:00, beat 5 compresses to a single ramp flip plus the badge change (~1:00).

---

## Prerequisites

Ordered. Items 1–3 are hard blockers: the demo cannot be performed without them.

### 1. UI "run this agent" control — new

Beats 0 and 2 cannot start their agents from the browser today. The UI can only start **floods**, and the flood agent is `incident-triage` — tier 1, deliberately toolless. The only agent launchable from the UI is the one that touches no AgentCore services at all.

- New API route accepting agent id, tenant and prompt.
- **It must write a `Job` row.** `dos agent test` does not, which is why CLI-started sessions are invisible in the UI's session list, absent from p95, and missing from queued counts (`docs/DECISIONS.md`, 2026-09-02).
- Small UI panel with agent picker, tenant picker and a prompt field pre-fillable from the demo script.

### 2. E8.1 T5 — blocking boto3 starving the worker event loop

Consciously accepted on 2026-09-02 when the flood was a standalone beat. **That decision no longer holds:** the cold-open flood runs underneath beats 0–3, so the API and worker are under load during every AgentCore beat — the exact condition that made the System Controls unresponsive for minutes.

Fix per the decision entry's ranked options: wrap the flood hot path in `asyncio.to_thread(...)` and set an explicit `max_concurrent_activities`; the canonical alternative is sync activities with a `ThreadPoolExecutor`. No `BUILD_ID` bump for either.

The API half of this was already fixed (option B, `app/demo.py`) but **remains unverified under load** — the measurement window captured an idle system only.

### 3. E8.1 T0 — consecutive-session SSE drop

Beats 0, 1, 2 and 3 are four sessions in a row on one page, which is precisely the reproduction case (four rounds is what reproduces reliably). Still not root-caused. Two of three causes were fixed on 2026-09-01; the third drops a stream mid-session.

### 4. Reset must purge tenant memory

Every flood job writes a memory event for its tenant. Measured 2026-09-02: acme, globex and initech each held five near-identical `incident-triage` summaries about `checkout-api`. Beat 0 on acme would therefore open by recalling load-test noise unrelated to any invoice, undermining the strongest AgentCore moment.

`DeleteEvent` exists on the `bedrock-agentcore` data plane, so this is buildable. Extend `scripts/reset.py` (currently job rows only) to purge memory events, then re-seed.

### 5. Demo setup script — new

Distinct from reset. After a reset, run two real `invoice-exception` sessions for the beat-0 tenant (different vendors, one settled, one held) so beat 0's recall surfaces genuine, relevant prior decisions.

### 6. Preflight and runbook — SSO

An expired SSO token kills every beat simultaneously: Bedrock, Memory, Guardrails, DynamoDB and the sandbox all resolve through the same credential chain. This happened twice during development (E6.1, and again 2026-09-02).

- Preflight asserts credentials are valid **and** reports remaining token lifetime.
- The runbook carries an explicit "refresh immediately before walking on stage" step.
- Note that `lru_cache`d boto3 sessions mean the **worker must be restarted** after a re-login, not just the token refreshed.

### 7. Remaining E8.1 items

T1 (Mockoon coverage audit), T2 (this script, written click-by-click after a real timed run), T3 (Playwright-recorded full run), T4 (the rest of reset: Mockoon buckets, fairness restored to ON, kill switch disarmed, ramp cleared).

---

## Risks

| Risk | Mitigation |
|---|---|
| Proof 1's numbers are untrustworthy until E8.1 T5 is fixed | Prerequisite 2. If it slips, beat 4 must be re-scoped or the numbers presented with an explicit caveat — not presented as clean |
| Guardrail block is hard to trigger reliably | Fix the exact prompt in the script; rehearse it; treat any improvisation as a failure mode |
| Memory console may not render events | Verify before writing narration; fall back to the establishing shot only |
| Four consecutive sessions on one page | Prerequisite 3. Interim workaround: reload between beats, which costs ~3s each and is visible but survivable |
| Account ID and resource names on a conference screen | Accepted deliberately; not secret |
| No offline fallback (`docs/DECISIONS.md`, 2026-08-21) | E8.1 T3's recorded run is the in-window fallback for AWS/network flakiness |

---

## Out of scope

E9 (deployment), E10.1 T5 (Code Exchange publication), the physical ten-metre projector check, and a presenter-narrated manual recording.
