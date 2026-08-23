# Backlog — Durable Agent Control Plane

Epic → User Story → Task. Work top to bottom; epics are ordered so that each one leaves the demo in a runnable state.

Status: `[ ]` todo · `[~]` in progress · `[x]` done · `[!]` blocked

**Sequencing rule:** E1–E4 must be complete before any UI polish. The proofs are the product; the desktop metaphor is how they are read.

---

## E0 — Foundations

*Goal: a worker runs a trivial durable job end to end, against real AWS.*

### Story E0.1 — Repo and dev loop
As a developer, I can clone the repo and run everything against real AWS (DynamoDB, Lambda sandbox) plus a standalone local Mockoon.

**Acceptance:** worker + API + UI all start against Temporal Cloud (never a local server); a hello-world job completes and is visible in the Temporal Cloud UI. No Docker Compose, no offline fallback (see `docs/DECISIONS.md`, 2026-08-21).

- [x] T1 Scaffold repo layout per CLAUDE.md §6; `uv` project, Python 3.12
- [x] T2 ~~Docker Compose: Mockoon, DynamoDB Local, sandbox image~~ — superseded; Mockoon runs standalone locally, DynamoDB and sandbox target real AWS, no Docker Compose needed
- [x] T3 Config module — env-driven, local vs cloud, no hardcoded ARNs
- [x] T4 Makefile / task runner for the §10 commands
- [x] T5 CI: lint, type-check, pytest

### Story E0.2 — Data model
As a developer, I have one place that defines tenants, agents, jobs, and idempotency records.

**Acceptance:** DynamoDB table created manually per `docs/AWS_SETUP.md`; `make verify` round-trips every item type against the real table.

- [x] T1 Single-table design; document key schema in `docs/DECISIONS.md`
- [x] T2 Pydantic models: `Tenant`, `AgentPackage`, `Job`, `JobResult`, `IdempotencyRecord`
- [x] T3 Repository layer against real AWS DynamoDB
- [x] T4 Create the table in AWS (manual — `docs/AWS_SETUP.md`), then `make verify` — done 2026-08-21, all item types round-trip

---

## E1 — Durable agent runtime

*Goal: one generic workflow runs a real Strands agent on Bedrock.*

### Story E1.1 — `AgentJobWorkflow`
As a platform, I run any agent through one PINNED workflow type.

**Acceptance:** a job runs to a typed result; `make replay` passes; no agent-specific code in the workflow.

- [x] T1 Read `temporal-developer` refs: python.md, determinism.md, ai-patterns.md, priority-fairness.md, versioning.md
- [x] T2 Read the `contrib.strands` README and installed source — actual API recorded in `docs/DECISIONS.md`
- [x] T3 Implement `AgentJobWorkflow` with `versioning_behavior=PINNED`
- [x] T4 Wire `StrandsPlugin` on the **client** (worker inherits it — passing both breaks; see `docs/DECISIONS.md`)
- [x] T5 Bedrock model registry by name (`bedrock-claude`, `bedrock-nova`)
- [ ] T6 Structured output via `structured_output_model` — `output_model` now exists in the manifest schema (E2.1) but is not yet wired into `TemporalAgent`; needs a way to resolve a model *name* to a Pydantic class inside the sandbox
- [x] T7 `scripts/replay.py` / `make replay` — replays real Temporal Cloud histories

**Verified 2026-08-21:** `make verify-agent` ran a real job (Bedrock Claude Sonnet 5 via the Strands loop) to a typed `JobOutcome`, `describe()` confirmed `VERSIONING_BEHAVIOR_PINNED` on `agent-control-plane.v1`; `make replay` clean.

### Story E1.2 — Tools as activities
As an agent, my tool calls are durable, retried, and idempotent.

**Acceptance:** a tool call survives worker restart without re-executing.

- [x] T1 `activity_as_tool` wiring for package-declared tools (`app/activities/catalog.py`)
- [x] T2 Retry policies and timeouts per tool class (consequential vs read-only)
- [x] T3 **Idempotent payment activity** backed by a DynamoDB idempotency key — proof 3 depends on this
- [x] T4 `make verify-payment` — duplicate delivery does not double-execute, verified against the real provider mock

**Verified 2026-08-21:** `make verify-payment` (same `activity_id` retried → provider count unchanged, `deduplicated=True`; different `activity_id` → goes through). `make verify-tool-call` (real workflow, Claude chose `issue_payment`, it appears in history as a scheduled activity, provider count +1 exactly). `make replay` clean.

**Note:** the kill-at-tool-boundary rehearsal that proves this *through a real worker crash* is E6.2 — this story proves the dedupe mechanism itself.

### Story E1.3 — Human interrupt
As an operator, I can redirect an agent mid-reasoning without losing work.

**Acceptance:** interrupt fires before an irreversible tool; a signal resumes the same session; history shows one continuous execution.

- [x] T1 `BeforeToolCallEvent` → `event.interrupt(...)` on approval-policy match (`app/workflows/approval.py`)
- [x] T2 Signal handler + `wait_condition` resume loop, plus a `pending_approval` query for the session pane
- [x] T3 `make verify-interrupt` — deny changes the outcome and blocks the payment; same run id, no restart

**Verified 2026-08-21** on `agent-control-plane:v2`: approve → provider +1; deny → provider +0 and the agent explains the block. `make replay` clean.

**Process note:** this story's behaviour change required a `BUILD_ID` bump (v1 → v2). `make replay` correctly failed until that happened — see `docs/DECISIONS.md`.

---

## E2 — Agent package system

*Goal: adding an agent needs zero workflow code and zero UI code.*

### Story E2.1 — Manifest and registry
As an author, I define an agent in a manifest plus an SOP file.

**Acceptance:** dropping a folder in `agents/` makes it appear in the store after worker restart.

- [x] T1 `manifest.yaml` schema + validation (`app/registry/manifest.py`, `app/registry/validate.py`)
- [x] T2 Registry loader + tool resolution against `TOOL_CATALOG` — MCP client registration lands with E7.1
- [x] T3 `procedure.sop.md` → system prompt, substituted per job at runtime
- [x] T4 `agents/_template/` + `CONTRIBUTING.md`

**Verified 2026-08-21:** validation catches an unregistered model, an unknown tool, a policy targeting an undeclared tool, an undeclared `{{placeholder}}`, and an unused parameter.

### Story E2.2 — `dos` CLI
As an author, I scaffold, validate, test, and publish an agent from the terminal.

**Acceptance:** the five commands in CLAUDE.md §10 work; publish makes the agent installable without a redeploy.

- [x] T1 `init` — scaffold from template
- [x] T2 `validate` — manifest resolves, tools exist, SOP placeholders declared
- [x] T3 `test` — runs a real job against Temporal Cloud
- [x] T4 `publish` — write registry row, no redeploy
- [x] T5 Install/uninstall per tenant — `repo.install_agent`/`uninstall_agent` (validates both tenant and agent exist), `dos tenant install`/`uninstall` CLI
- [x] T6 `list` — packages on disk

**Note:** invoked as `uv run dos ...`, wrapped by the `make agent-*` targets. The earlier `python -m cli.main` workaround is retired — the repo directory was renamed to drop its trailing space, which fixed editable installs (`docs/DECISIONS.md`, 2026-08-21).

### Story E2.3 — The four reference agents
As a demo, I have four real, runnable agents.

**Acceptance:** all four execute real jobs; none are stubs.

- [x] T1 Invoice Exception (Acme, native, tier 2) — the on-stage agent; authored via `agent init`, validated, published, and run for real
- [ ] T2 Incident Triage (Initech, native, tier 1) — the flood generator
- [ ] T3 Dispute Resolution (Globex, native, tier 2)
- [ ] T4 VendorCheck (hosted, tier 3) — AgentCore Runtime lane, manifest only

---

## E3 — Multi-tenancy and fairness → **PROOF 1**

### Story E3.1 — Tenants and priority
As a platform, tenants are isolated and tiered.

**Acceptance:** three tenants with distinct priority tiers, memory namespaces, and tool scopes.

- [x] T1 Tenant model: priority key, fairness weight, S3 prefix, memory namespace — the model and DynamoDB CRUD already existed (`app/registry/models.py`, `repository.py`) and passed `make verify` before this story; nothing to build here
- [x] T2 Start workflows with `Priority(priority_key, fairness_key, fairness_weight)` — `app/registry/priority.py`'s `resolve_priority()` reads the real tenant row; wired into `dos agent test`, replacing the hardcoded literal it used before
- [!] T3 Per-tenant tool authorization via AgentCore Identity; denial visible in the session pane — deferred out of E3.1: no session pane exists yet (E5 unbuilt) and nothing in the codebase touches AgentCore Identity. Revisit once E5 lands, or fold into E7 (AWS services).

**Verified 2026-08-21:** three demo tenants (acme/globex/initech) seeded into the real DynamoDB table via `dos tenant add`. `make verify-tenant-priority` — two tenants with different `priority_key`/`fairness_weight` started real workflows on Temporal Cloud; `describe()` confirmed `workflow_execution_info.priority` matched each tenant's registry row exactly and differed between the two. `dos agent test` runs against a real seeded tenant end to end (Bedrock-backed); an unrecognised tenant fails clearly instead of defaulting silently. `make replay` clean (no workflow code changed).

### Story E3.2 — Fairness proof instrumentation
As a presenter, I can show fairness working by turning it off and on.

**Acceptance:** fairness off + 200-job flood → protected tenant p95 visibly collapses; fairness on → holds. Live, no restart.

- [x] T1 Per-tenant p95 wait metric, computed from real workflow timings — `app/registry/metrics.py`'s `tenant_wait_p95()`, from real `Job.created_at`/`started_at` rows; the latter stamped by a new `mark_job_started` activity called at the top of `AgentJobWorkflow.run` (workflow behaviour change → `BUILD_ID` v3→v4)
- [x] T2 Runtime fairness on/off toggle (CLI only — API deferred to E4.1, confirmed at Inception) — a persisted `FairnessSetting` DynamoDB row; `resolve_priority()` is the single place that checks it, so every caller inherits the toggle
- [x] T3 `dos demo flood` — parameterised tenant and count, seeds a dedicated lightweight load-generation agent (`flood-load-agent`), submits concurrently (not sequentially) so it actually builds backlog
- [~] T4 Rehearse: confirm the effect is legible within 30 seconds — mechanism verified end-to-end at reduced scale (20+4+4 jobs, real AWS/Temporal Cloud); direction is correct (fairness on separated protected tenants' p95 well below the flooder's, fairness off pulled them up to nearly match it). Full 200-job dress rehearsal intentionally deferred — every flood job is a real Bedrock call, so repeated full-scale runs cost real money; schedule that deliberately closer to the actual event rather than as routine verification.

**Verified 2026-08-21:** with fairness on, a concurrent flood (initech ×20, acme ×4, globex ×4) produced p95 waits of 10.51s / 6.65s / 3.60s — the protected tenants held well below the flooder. With fairness off, the same shape produced 10.63s / 10.27s / 9.72s — acme and globex collapsed to nearly the flooder's own wait, true FIFO. `make replay` clean against 20 real v4 histories. Along the way, caught and fixed a real bug: the new `mark_job_started` activity wasn't registered on the worker (`app/worker.py`) — the workflow's non-fatal try/except around it meant jobs still succeeded, but no wait data was recorded until fixed.

---

## E4 — Control plane API and streaming

### Story E4.1 — API and SSE bus
As the UI, I get live job and fleet state without polling Temporal.

**Acceptance:** API is the sole Temporal client; UI receives updates over SSE.

- [x] T1 FastAPI: tenants, agents, jobs, install, fleet metrics — `app/api/routes/{tenants,agents,jobs,metrics}.py`. Agents route reads the registry (`list_agent_packages()`, latest version per id), not disk — disk-based `discover()` would break once the API is deployed without `agents/` in its bundle (E2.1's design). Jobs route reuses the existing tenant-scoped gsi1 query, no new DynamoDB surface.
- [x] T2 SSE event bus — **corrected 2026-08-23** to use Temporal's own `temporalio.contrib.workflow_streams` (Experimental) instead of a hand-rolled bus. First attempt ported `durable-agentic-harness`'s in-process `EventBus` + a token-protected `/internal/events` HTTP hop; the user pointed at `temporalio/samples-python`'s `workflow_streams` sample, which ships exactly this durable, offset-addressed mechanism natively — no separate bus (not durable, dies with the API process), no custom auth hop. `AgentJobWorkflow` now hosts a `WorkflowStream` (constructed in `@workflow.init`, per the sample) and publishes `UIEvent`s directly from workflow code at its existing natural moments (job started, approval pending, approval resumed, job finished — richer per-token detail stays E4.2's job). `GET /api/jobs/{id}/events` subscribes straight to the workflow via `WorkflowStreamClient`, decoding through the client's own data converter. Workflow behaviour change → `BUILD_ID` v5→v6.
- [x] T3 Demo control endpoints: flood, fairness — `POST /api/demo/flood`, `POST /api/demo/fairness`, both wrapping the same `app/demo.py` logic the CLI uses (extracted, not duplicated). `ramp`/`kill` omitted, not stubbed — those CLI commands don't exist yet (E6, unbuilt), same precedent as E3.1's T3 deferral.

**Verified 2026-08-23:** full pipeline confirmed live against real AWS/Temporal Cloud — started a real job, subscribed to its SSE stream first, watched `job_started` then `job_finished` arrive in order with correct payloads; a wrong internal token got a real 401. All read endpoints (tenants, agents, jobs, fleet metrics) confirmed against real DynamoDB data; install/uninstall confirmed via the API including the 404 path for an unknown agent. `POST /api/demo/flood` submitted real jobs whose Job rows and p95 metric showed up correctly. `make replay` clean against 3 real v5 histories.

### Story E4.2 — Token streaming
As an operator, I watch the agent reason in real time.

**Acceptance:** tokens appear in the session terminal as the agent runs; interrupt is possible mid-stream.

- [x] T1 `WorkflowStream` wiring — done early, as part of E4.1's corrected T2 (Experimental, labelled per CLAUDE.md §2). `AgentJobWorkflow` already hosts the stream and publishes coarse-grained events; this story's remaining work is the token-level `delta`/`tool_call`/`guardrail` topics on top of it, published from inside the model-call activity via `WorkflowStreamClient.from_within_activity()` (see `workflow_streams`'s `llm_activity.py` sample).
- [ ] T2 Session terminal event shapes: token, tool call, guardrail verdict, memory recall
- [ ] T3 Fallback to polled state if streams are unavailable — must not block a proof

---

## E5 — Desktop OS UI

*Goal: it reads as an operating system in three seconds.*

### Story E5.1 — Shell and panes
As an audience member, I recognise an app store, a process monitor, and a terminal.

**Acceptance:** four panes always on; legible from ten metres; no fake data.

- [ ] T1 Layout shell + status strip (workers, sandboxes, S3 offloaded, ramp state)
- [ ] T2 Agent store: installed vs available, native/hosted badge, Install
- [ ] T3 Process monitor: per-tenant lanes, queued/running, p95, priority tier
- [ ] T4 Session terminal: stream, tool calls, guardrail verdicts, version badge, approve/redirect
- [ ] T5 System controls: fairness toggle, ramp slider, kill worker

### Story E5.2 — Stage legibility pass
As a presenter, every state change is visible without narration.

**Acceptance:** a dry run recorded and viewed at 10 m on a conference-size screen.

- [ ] T1 Type scale, contrast, colour audit
- [ ] T2 Animate the three state changes that carry the proofs
- [ ] T3 Fix anything unreadable in the recording

---

## E6 — Versioning and chaos → **PROOFS 2 and 3**

### Story E6.1 — Worker versioning
**Acceptance:** deploy v2 with 20 sessions in flight; v1 sessions finish on v1; new sessions start on v2; both visible side by side.

- [ ] T1 Worker Deployment Versions; `default_versioning_behavior=PINNED`
- [ ] T2 Ramp control (API + CLI)
- [ ] T3 Version badge per session in the UI
- [ ] T4 Ship a genuinely different v2 of Invoice Exception, so the streams visibly differ
- [ ] T5 Test: in-flight pinning holds under ramp

### Story E6.2 — Armed kill and resume
**Acceptance:** kill fires at a tool boundary right after the payment activity; restart resumes at the same turn; payment counter still reads 1.

- [ ] T1 Armed kill — fires at next tool boundary, not on a timer
- [ ] T2 Side-effect counter surfaced from the mocked payment service, always visible
- [ ] T3 Restart path; sessions resume mid-turn
- [ ] T4 Test: no duplicate payment across kill/restart
- [ ] T5 Rehearse ten times — this is the closer and must be live

---

## E7 — AWS services

### Story E7.1 — Gateway, Memory, Guardrails
- [ ] T1 `TemporalMCPClient` against AgentCore Gateway; tools re-listed per turn
- [ ] T2 AgentCore Memory, tenant-scoped recall
- [ ] T3 Bedrock Guardrails as a deterministic activity between proposal and commit
- [ ] T4 Verify region availability; document gaps for India accounts

### Story E7.2 — Isolation and payloads
- [ ] T1 AgentCore Code Interpreter sandbox lane, invoked from a Temporal Activity (see `docs/DECISIONS.md`)
- [ ] T2 Sandbox as child workflow; killing one does not affect others
- [ ] T3 S3 External Storage claim-check — label as Preview
- [ ] T4 Storage pane: payload size vs history size

### Story E7.3 — Hosted lane
- [ ] T1 `invoke_hosted_agent` activity against AgentCore Runtime
- [ ] T2 Register a hosted agent by ARN from the store, no repo access
- [ ] T3 Document the durability tradeoff: per-invocation, not per-turn

### Story E7.4 — Multi-agent collaboration *(build, not stage)*
- [ ] T1 Strands Swarm handoff inside one job — invoice → fraud specialist
- [ ] T2 Nexus call to a second namespace as the cross-team variant
- [ ] T3 `docs/MULTI_AGENT.md` — the inside-a-job vs between-jobs rule

---

## E8 — Demo hardening

### Story E8.1 — Rehearsal
**Acceptance:** full 8-minute run against real AWS, rehearsed end to end. No offline fallback (`docs/DECISIONS.md`, 2026-08-21) — venue needs internet/AWS access.

- [ ] T1 Mockoon collections for every external service
- [ ] T2 `docs/DEMO_SCRIPT.md` — click-by-click, timed
- [ ] T3 Full-run recording as in-window fallback (in case of AWS/network flakiness, not as an offline substitute)
- [ ] T4 Reset script — return to clean state between deliveries

---

## E9 — Deploy

### Story E9.1 — AWS deployment
- [ ] T1 SAM: DynamoDB, IAM roles (Lambda worker, Temporal Cloud → Lambda invoke), S3
- [ ] T2 Workers as Serverless Workers on Lambda (the only worker lane); Temporal Cloud auth via API key, per `docs/DECISIONS.md`
- [ ] T3 API + Mockoon on one App Runner service — one container, reverse-proxied so both the API and the Lambda worker can reach Mockoon
- [ ] T4 UI on Amplify Hosting

---

## E10 — Open source release

### Story E10.1 — Make it forkable
**Acceptance:** a stranger adds an agent by following the README alone.

- [ ] T1 README: thesis, architecture, quickstart
- [ ] T2 `CONTRIBUTING.md` — copy the template, edit the manifest, write the SOP
- [ ] T3 Worked tier-1 example (Returns Triage) end to end
- [ ] T4 Label every preview feature with its status
- [ ] T5 Publish to Temporal Code Exchange
