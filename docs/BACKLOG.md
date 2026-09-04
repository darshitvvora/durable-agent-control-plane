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
- [x] T6 Structured output via `structured_output_model` — delivered by **E6.1 T4** (2026-08-24): `app/registry/output_models.py`'s `OUTPUT_MODEL_CATALOG` resolves a manifest `output_model` name to a Pydantic class inside the sandbox (pure type lookup, no I/O), passed to `TemporalAgent`. `invoice-exception` v2+ declares `output_model: InvoiceDecision`; `dos agent validate` rejects an unknown name and rejects the field entirely on tier 3. Ticked 2026-09-01 after audit — it was done, never checked off.
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
- [x] T2 Incident Triage (Initech, native, tier 1) — the flood generator. Tier 1 means SOP only, no tools: it *recommends* a rollback rather than performing one, which resolves the stub's own tier-1-plus-a-rollback-tool contradiction and keeps 200 concurrent flood jobs cheap. `dos demo flood` now uses this real published agent instead of the code-seeded `flood-load-agent`, which is deleted.
- [x] T3 Dispute Resolution (Globex, native, tier 2) — two new tools: `fetch_dispute_evidence` (read-only, first user of the catalog's `READ_ONLY` retry class) and `submit_dispute_response` (consequential, idempotent). Approval policy gates filings above `review_threshold_usd`.
- [x] T4 VendorCheck (hosted, tier 3) — delivered in **E7.3** (2026-08-29), which owns the hosted lane: package at `agents/vendorcheck/`, hosted agent source at `infra/hosted/vendorcheck/main.py`, invoked via `invoke_hosted_agent` against the real deployed runtime. Meets this story's "executes a real job" bar: `dos agent test vendorcheck --tenant acme` returned a correct **review** verdict for Initech Supply (unverified beneficial owner + three adverse media) through the published on-disk package, and `make verify-hosted` covers the register-by-ARN path.

**Verified 2026-08-23** (`make verify-agents`, new): all three native reference agents run real jobs on Temporal Cloud against real Bedrock, each reaching the outcome its SOP specifies — incident-triage named `checkout-api v412` as the suspect deploy with zero tool activities scheduled; dispute-resolution called `fetch_dispute_evidence` then `submit_dispute_response` for exactly one filing; invoice-exception settled a below-threshold invoice via `issue_payment`. Proof 3 re-verified after extracting the shared idempotency helper (`make verify-payment`), plus `make verify-tool-call`, `make verify-interrupt`, and `make replay` clean against 18 v7 histories.

---

## E3 — Multi-tenancy and fairness → **PROOF 1**

### Story E3.1 — Tenants and priority
As a platform, tenants are isolated and tiered.

**Acceptance:** three tenants with distinct priority tiers and memory namespaces.

- [x] T1 Tenant model: priority key, fairness weight, S3 prefix, memory namespace — the model and DynamoDB CRUD already existed (`app/registry/models.py`, `repository.py`) and passed `make verify` before this story; nothing to build here
- [x] T2 Start workflows with `Priority(priority_key, fairness_key, fairness_weight)` — `app/registry/priority.py`'s `resolve_priority()` reads the real tenant row; wired into `dos agent test`, replacing the hardcoded literal it used before

**Story complete** as of 2026-09-01, when its one remaining task was removed from the project scope rather than built.

**Verified 2026-08-21:** three demo tenants (acme/globex/initech) seeded into the real DynamoDB table via `dos tenant add`. `make verify-tenant-priority` — two tenants with different `priority_key`/`fairness_weight` started real workflows on Temporal Cloud; `describe()` confirmed `workflow_execution_info.priority` matched each tenant's registry row exactly and differed between the two. `dos agent test` runs against a real seeded tenant end to end (Bedrock-backed); an unrecognised tenant fails clearly instead of defaulting silently. `make replay` clean (no workflow code changed).

### Story E3.2 — Fairness proof instrumentation
As a presenter, I can show fairness working by turning it off and on.

**Acceptance:** fairness off + 200-job flood → protected tenant p95 visibly collapses; fairness on → holds. Live, no restart.

- [x] T1 Per-tenant p95 wait metric, computed from real workflow timings — `app/registry/metrics.py`'s `tenant_wait_p95()`, from real `Job.created_at`/`started_at` rows; the latter stamped by a new `mark_job_started` activity called at the top of `AgentJobWorkflow.run` (workflow behaviour change → `BUILD_ID` v3→v4)
- [x] T2 Runtime fairness on/off toggle (CLI only — API deferred to E4.1, confirmed at Inception) — a persisted `FairnessSetting` DynamoDB row; `resolve_priority()` is the single place that checks it, so every caller inherits the toggle
- [x] T3 `dos demo flood` — parameterised tenant and count, seeds a dedicated lightweight load-generation agent (`flood-load-agent`), submits concurrently (not sequentially) so it actually builds backlog
- [!] T4 Rehearse: confirm the effect is legible within 30 seconds — **regressed and partly repaired 2026-09-02.** A 12-job flood killed ten of twelve jobs: E7.1 T2 (2026-08-28) added `recall_tenant_memory` to every job *after* this story was last verified (2026-08-21), and a flood is N concurrent reads of one tenant's single Memory stream, which exhausted the activity's 10s/2-attempt budget. Fixed (non-fatal recall + 30s/3 attempts, `BUILD_ID` v17→v18) and verified at 62 concurrent jobs with zero failures. **The proof itself is still not demonstrated:** a paired 12/4/4 run gave fairness ON `4.77 / 2.17 / 1.81`s vs OFF `6.73 / 4.59 / 2.85`s — right direction (acme +112%), but the flooder's own p95 also moved, so the rounds were not load-equivalent, and OFF never collapsed the tenants together the way the 2026-08-21 run did. Needs the paired 50 + 4 + 4 rounds with a reset between them. Original note: mechanism verified end-to-end at reduced scale (20+4+4 jobs, real AWS/Temporal Cloud); direction is correct (fairness on separated protected tenants' p95 well below the flooder's, fairness off pulled them up to nearly match it). Full 200-job dress rehearsal intentionally deferred — every flood job is a real Bedrock call, so repeated full-scale runs cost real money; schedule that deliberately closer to the actual event rather than as routine verification.

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

- [x] T1 `streaming_topic` + `WorkflowStream` wiring (Experimental, labelled per CLAUDE.md §2) — the stream itself landed with E4.1's corrected T2; this story added `TemporalAgent(streaming_topic="model_stream")`, which is first-class in the Strands plugin: it switches the model call from `invoke_model` to `invoke_model_streaming`, which publishes each Strands `StreamEvent` onto this workflow's stream via `WorkflowStreamClient.from_within_activity()`. No hand-written streaming activity — the plugin owns that. Second topic on the existing stream, since the payload type differs from `job_events`.
- [x] T2 Session terminal event shapes: token + tool call, then **memory recall** (E7.1 T2) and **guardrail verdict** (E5.1 T4, 2026-09-01) once their services existed to emit them. Originally scoped as: token + tool call done; guardrail verdict and memory recall deferred — Bedrock Guardrails (E7.1 T3) and AgentCore Memory (E7.1 T2) are unbuilt, so nothing can emit them yet; they land with their own services. Translation from raw `StreamEvent` happens in the API's SSE route (`translate_stream_event`), not the UI, so Strands' wire format never reaches the UI contract. Also emits `reasoning` (Claude's extended thinking arrives on `contentBlockDelta.delta.reasoningContent.text`, not `.text`) as a distinct kind rather than dropping it.
- [x] T3 Fallback to polled state if streams are unavailable — server half built: `GET /api/jobs/{id}/state` returns real execution status, worker deployment version, and pending approval, from a live `describe()` plus the workflow's own query. The UI's switch-on-SSE-failure logic lands with the session terminal (E5.1 T4), since there is no UI to switch yet.

**Verified 2026-08-23** (`make verify-streaming`, new): a real tool-using job streamed 18 real `token` events accumulating to coherent text, plus a `tool_call` event naming `issue_payment`, all over the real SSE route; `/state` answered `RUNNING` / `agent-control-plane:v7` mid-flight. Workflow behaviour change → `BUILD_ID` v6→v7; history now shows `invoke_model_streaming` in place of `invoke_model`. All three proofs re-verified on v7 (payment idempotency, tool-call, approve/deny interrupt — the last covers the acceptance criteria's "interrupt is possible mid-stream"), plus `make verify-tenant-priority`; `make replay` clean against 5 v7 histories.

---

## E5 — Desktop OS UI

*Goal: it reads as an operating system in three seconds.*

### Story E5.1 — Shell and panes
As an audience member, I recognise an app store, a process monitor, and a terminal.

**Acceptance:** four panes always on; legible from ten metres; no fake data.

- [x] T1 Layout shell + status strip — workers and job-status counts are real (Temporal `describe_task_queue` poller count, `count_workflows` grouped by status via new `app/registry/fleet.py`); **sandboxes, S3 offloaded, and ramp state omitted**, not stubbed — their backends (E7.2, E6.1) don't exist, and CLAUDE.md §7 rules out fake numbers on a conference screen
- [x] T2 Agent store: installed vs available (per selected tenant), native/hosted badge, tier + gated badge, Install/Remove — wired to the real `/api/agents` + `/api/tenants/{id}/install`
- [x] T3 Process monitor: per-tenant lanes, running/queued/p95/tier, sorted busiest-first. Running counts needed a real fix, not just a UI decision: nothing previously marked a job *finished*, so DynamoDB-only counts would only ever climb — resolved by querying Temporal's own visibility store instead (`count_workflows` grouped by tenant), which needed a new `TenantId` custom search attribute added to the Temporal Cloud namespace (one-time `tcld` step, see `docs/AWS_SETUP.md`). Renders `?` rather than `0` if that attribute isn't registered yet.
- [x] T4 Session terminal: stream, tool calls, version badge, approve/deny, **and guardrail verdicts** — the last of these completed 2026-09-01 once E7.1 T3 gave it something real to show. `GuardrailGate` hands each verdict back via an `on_verdict` callback and `AgentJobWorkflow` publishes it as a `guardrail` `UIEvent` on the existing `job_events` topic; the API needed no change. A pass renders quietly, a block renders as an alert bar naming the tool and reason. `BUILD_ID` v16→v17.
- [x] T5 System controls: fairness toggle, flood control, **ramp slider and kill worker** — the last two were wired to their real backends by **E6.2** (2026-08-28) once E6 existed to back them. Ticked 2026-09-01 after audit — it was done, never checked off.

**New:** `TenantId` Keyword search attribute added to the demo Temporal Cloud namespace via `tcld namespace search-attributes add` — a one-time, human-run setup step (`docs/AWS_SETUP.md`), consistent with CLAUDE.md §2's "AWS actions are manual and documented" rule extended to Temporal Cloud namespace config. Every job start now also carries this attribute (`app/registry/priority.tenant_search_attributes()`), client-side only — no `BUILD_ID` bump.

**Verified 2026-09-01** (`make verify-guardrail`, new): two real jobs on the real Bedrock Guardrail over the real SSE route — a clean filing streamed `blocked=false` and filed exactly once; a rationale quoting a cardholder verbatim streamed `blocked=true` with a reason and the dispute service recorded **zero** filings for it. Three consecutive clean runs. Confirmed in the real UI with Playwright: the terminal rendered `→ submit_dispute_response` followed by `✕ guardrail blocked submit_dispute_response — Guardrail blocked.` live. `make replay` clean at 14 v17 histories, `make verify-agents` clean, `make e2e` 6/6, ruff + mypy clean.

Proving the *block* took five attempts and produced a finding worth carrying: the guardrail's `OffPolicyPayment` denied topic is nearly unreachable through a model turn, because its definition overlaps almost exactly with the model's own refusal boundary — Claude and Nova both decline to file such a rationale, so the gate never sees a proposal. Full account in `docs/DECISIONS.md`.

**New:** Playwright end-to-end suite (`ui/e2e/shell.spec.ts`, `make e2e`) drives the real UI against the real API/Temporal Cloud/Bedrock — no mocks, per the 2026-08-21 decision anticipating this. Lives under `ui/e2e/` rather than the originally-noted `scripts/e2e/`, since it's an npm/Playwright-native project colocated with the frontend rather than a Python script; `make e2e` still gives it a Makefile entry point. Six specs cover: all four panes present, a real worker count, real agent/tier badges, one lane per tenant with real fairness weights, the 13px stage-legibility floor, and a full flood-to-live-stream-to-completion run through the UI's own controls.

### Story E5.2 — Stage legibility pass
As a presenter, every state change is visible without narration.

**Acceptance:** a dry run recorded and viewed at 10 m on a conference-size screen.

- [x] T1 Type scale, contrast, colour audit — measured via computed-style introspection (Playwright `browser_evaluate`), not eyeballed: iterated until the smallest rendered text anywhere in the shell was 13px (secondary labels only — column headers, units) and the two numbers the proofs turn on (p95 wait, lane load) are the largest things on screen. `make e2e`'s stage-floor spec makes this a regression guard, not a one-time check.
- [x] T2 Animate the three state changes that carry the proofs — a FLIP transform transition on Process Monitor's rows (`useLaneFlip`, `ProcessMonitor.tsx`) so a lane overtaking another eases into position instead of snapping (the server already sorts lanes busiest-first, per E3.2; this is what finally makes that visible as motion); a `flip-flash` keyframe on the fairness ON/OFF readout (`SystemControls.tsx`); a `badge-in` keyframe on the worker-version badge's first appearance (`SessionTerminal.tsx`). All three are remount-triggered (`key={value}`) CSS animations, not a new dependency — consistent with `index.css`'s existing rule that only deliberate state flips animate, never streamed data arriving.
- [~] T3 Fix anything unreadable in the recording — `make e2e`'s 6 specs pass clean (including a real flood-and-stream run exercising the new lane FLIP), and a manual Playwright walkthrough at 1400×1400 confirmed the System Controls pane's new sections render correctly. A true conference-scale (10m, projector) dry run is still open — genuinely needs physical hardware, out of scope for a build session.

---

## E6 — Versioning and chaos → **PROOFS 2 and 3**

### Story E6.1 — Worker versioning
**Acceptance:** deploy v2 with 20 sessions in flight; v1 sessions finish on v1; new sessions start on v2; both visible side by side.

- [x] T1 Worker Deployment Versions; `default_versioning_behavior=PINNED` — already done since E1.1 (`app/worker.py`); confirmed it matches `temporalio/samples-python`'s `worker_versioning/workerv2.py` exactly
- [x] T2 Ramp control (API + CLI) — `app/registry/deployment.py` wraps the raw `workflow_service` RPCs (`set_worker_deployment_current_version`, `set_worker_deployment_ramping_version`; no high-level `Client` method exists for either). `dos demo ramp --version --percent` / `GET,POST /api/demo/ramp` / `POST /api/demo/ramp/clear`. `--percent >=100` calls set-current instead of a 100% ramp, matching the Temporal CLI's own documented distinction between the two.
- [x] T3 Version badge per session in the UI — already done since E4.2/E5.1 (`/api/jobs/{id}/state`'s `worker_version`, rendered in `SessionTerminal.tsx`)
- [x] T4 Ship a genuinely different v2 of Invoice Exception — a real `AgentJobWorkflow` change, not a cosmetic one (confirmed with the human at Inception): wired `structured_output_model` into `TemporalAgent` (E1.1 T6, previously an open gap), resolved from the manifest's `output_model` field via a new `OUTPUT_MODEL_CATALOG` (same resolve-or-fail shape as `TOOL_CATALOG`). `invoice-exception` v2 sets `output_model: InvoiceDecision` — same policy as v1, but the answer is a validated object instead of prose. `BUILD_ID` v7→v8.
- [x] T5 Test: in-flight pinning holds under ramp — new `make verify-pinning`, which spawns its own second worker process under a temporary Build ID and proves the real mechanism end to end: a session paused on approval stays pinned to its original build (zero worker cost while it waits) after `current` moves elsewhere mid-flight; a genuinely new session started after the move runs on the new build; approving the paused session lets it finish — still on its original build, never having moved.

**Verified 2026-08-24**, all against real Temporal Cloud: `make verify-versioning` (structured output — a settle decision on invoice INV-8001 came back as a validated `InvoiceDecision`, not prose; ramp control — set 25% toward a real prior build, confirmed via `describe_worker_deployment`, then cleared). `make verify-pinning` (the full pin-survives-a-deploy mechanism, described above). Since this touched every session's model construction, all three proofs were re-verified on v8 (payment idempotency, tool-call, approve/deny interrupt, streaming, tenant priority) and `make replay` is clean against 11 v8 histories.

**`make verify-agents` re-run interrupted mid-run by AWS SSO token expiry** (`"Token has expired and refresh failed"`) after a very long session — an environmental issue, not a code defect; Temporal Cloud auth (a separate API key) was unaffected throughout. Caught and fixed a real, separate test-script gap while diagnosing it: `scripts/verify_reference_agents.py` didn't handle Dispute Resolution's approval interrupt, which Mockoon's randomised evidence amount (E2.3 DECISIONS.md) can legitimately trigger above $500 — the agent paused correctly, exactly as designed, and the *test* just never approved it. Fixed to auto-approve like a reviewer would, same as `verify_interrupt.py`'s pattern. Full `make verify-agents` re-run pending a fresh AWS session (`aws sso login`) — the mechanism itself (all three agents, including the approval path) was already proven correct earlier in E2.3 and again mid-diagnosis here.

### Story E6.2 — Armed kill and resume
**Acceptance:** kill fires at a tool boundary right after the payment activity; restart resumes at the same turn; payment counter still reads 1.

- [x] T1 Armed kill — fires at next tool boundary, not on a timer — a `KillSwitch` DynamoDB record (same pattern as `FairnessSetting`), checked inside the shared `run_once` (`app/activities/idempotency.py`) right after the external call succeeds and before completion is recorded, then `os._exit(1)`. Deterministic, not a raced OS signal — considered and rejected an external container/process kill (see `docs/DECISIONS.md`).
- [x] T2 Side-effect counter surfaced from the mocked payment service, always visible — `GET /api/demo/payment-count` (`len(GET /payments)` against Mockoon's own CRUD bucket, which already counts every call with no dedupe by design); on the Status Strip via `/api/metrics/status`'s new `payment_count` field.
- [x] T3 Restart path; sessions resume mid-turn — inherent to Temporal once T1 existed to prove it; no new code.
- [x] T4 Test: no duplicate payment across kill/restart — `scripts/verify_kill_resume.py` / `make verify-kill-resume`; caught and fixed two real bugs in the *existing* idempotency mechanism, not just added test coverage (see `docs/DECISIONS.md`).
- [~] T5 Rehearse ten times — 3 clean automated back-to-back runs via `make verify-kill-resume` so far; a live, presenter-driven 10x rehearsal (manual `dos demo kill-worker` + `make worker` restart, on the actual demo stack) is still open, scheduled for the Operations dry-run pass.

CLI: `dos demo kill-worker --at-tool-boundary`, `dos demo payment-count`. UI: System Controls gained the kill-worker button and the ramp slider carried over from E6.1 (its backend existed, but was never wired into the UI until now).

**Verified 2026-08-28** against real Temporal Cloud, real DynamoDB, and real Mockoon: `make verify-kill-resume` (3 consecutive clean runs), plus re-verification of everything sharing the changed code path — `make verify-payment`, `make verify-tool-call`, `make verify-interrupt`, `make verify-agents` (covers dispute-resolution's `submit_dispute_response`, which shares `run_once`), and `make replay` clean against 13 histories. Proofs 1 and 2 were not re-run — nothing this story touched is on their path.

---

## E7 — AWS services

### Story E7.1 — Gateway, Memory, Guardrails
**All three AWS resources provisioned 2026-08-28** (human-run, per `docs/AWS_SETUP.md`): Guardrail `lqhh5fwndqoh` v1 READY, Memory `durable_agent_control_plane_tenant_recall-eUd6RC3Jts` ACTIVE, Gateway `durable-acp-gateway-twsmoz1de7` READY with its `vendor-directory` Lambda target READY. `.env` updated with all four values. Gateway smoke-tested end-to-end over raw MCP JSON-RPC (`initialize` → `tools/list` → `tools/call`) before writing any client code — `lookup_vendor_risk` for "Globex Retail" correctly returned the Lambda's canned risk data.

- [x] T1 `TemporalMCPClient` against AgentCore Gateway; tools re-listed per turn — `app/registry/mcp_servers.py` (workflow-side catalog, "vendor-directory" as a portable name) + `app/temporal_client.py`'s `mcp_client_registry()` (worker-side, the only place the real Gateway URL lives). `invoice-exception` v3 adds `mcp_servers: [vendor-directory]` and a SOP step requiring a risk lookup before deciding. Two real bugs caught before this was right — see `docs/DECISIONS.md`: a missing `mcp_clients=` argument to `StrandsPlugin`, and using the raw Gateway URL as the workflow-side `server` name instead of a portable catalog key. `BUILD_ID` v8→v9→v10 (v9 briefly had two incompatible shapes in the same build id — a process mistake, corrected by bumping again rather than left as a `make replay` gap).
- [x] T2 AgentCore Memory, tenant-scoped recall — `app/activities/memory.py` (`recall_tenant_memory`/`record_tenant_memory`, real `bedrock-agentcore` data-plane calls, `create_event`/`list_events`, not `retrieve_memory_records` since this Memory resource has no strategies). `actorId`/`sessionId` both = `tenant_id`, so every job for a tenant writes into one recallable stream. Recall runs once per job (before the SOP renders, appended as a "Recent history" section) and publishes the deferred `memory_recall` session-terminal event; write runs once after the job finishes, best-effort. `BUILD_ID` v10→v11.
- [x] T3 Bedrock Guardrails as a deterministic activity between proposal and commit — `app/activities/guardrail.py` (`apply_guardrail`, standalone `ApplyGuardrail` API) + `app/workflows/guardrail.py` (`GuardrailGate`, an *async* `BeforeToolCallEvent` hook next to `ApprovalGate` — confirmed Strands' `invoke_callbacks_async` supports mixed sync/async callbacks by reading source). Only `submit_dispute_response` is guarded, not `issue_payment` — see `docs/DECISIONS.md` for the two false positives that led there. `BUILD_ID` v11→v12→v13 (v12 added the hook for both tools; v13 narrowed to one, after the false positives were found).
*(A fourth task covering per-region availability gaps was dropped 2026-08-29: everything runs in `us-east-1` and the demo is global, so there is no second region to document gaps against.)*

**Verified 2026-08-28** against real Temporal Cloud/AWS/Gateway/Memory/Guardrails: `dos agent test invoice-exception` against low/medium-risk vendors and a $900 above-threshold approval flow, `make verify-agents` ×2 for stability (activity lists now show `recall_tenant_memory`/`record_tenant_memory` on every agent and `apply_guardrail` only on dispute-resolution's filing), all three proofs re-run repeatedly across v10→v13 (`verify-payment`, `verify-tool-call`, `verify-interrupt`, `verify-versioning`, `verify-pinning`, `verify-kill-resume`, `verify-streaming`, `verify-tenant-priority`), `make replay` clean at each step, finishing clean against 17 v13 histories. Three test-script bugs found and fixed along the way (not application bugs, per `docs/DECISIONS.md`): `verify_payment.py`'s fixed workflow id colliding with its own leftover data; a `verify-kill-resume` run intercepted by a leftover manually-started worker; `verify_reference_agents.py`'s fixed dispute/invoice ids colliding with the tenant's own new Memory recall across runs.

### Story E7.2 — Isolation and payloads

**T1/T2 done and verified 2026-08-29; T3/T4 code-complete but blocked on a human step — see `docs/DECISIONS.md`'s 2026-08-29 entry for the full story** (agent choice, the aioboto3/boto3 dependency conflict, the StrandsPlugin data-converter gotcha, a real `dispute.py` bug found and fixed, and a noted-but-out-of-scope kill-switch race).

- [x] T1 AgentCore Code Interpreter sandbox lane, invoked from a Temporal Activity — Dispute Resolution's new `analyze_dispute_risk` tool (`app/activities/sandbox.py`), not Incident Triage (proof 1's flood generator, must stay toolless). Real `bedrock-agentcore` boto3 API, confirmed against AWS docs. Verified via `dos agent test dispute-resolution` and `make verify-agents`.
- [x] T2 Isolation — resolved as activity-level isolation, not a child workflow (a child workflow would be a second workflow type, against CLAUDE.md §2/§11's one-generic-workflow rule). New `scripts/verify_sandbox_isolation.py`: kills the worker mid-Code-Interpreter-session with two concurrent dispute jobs running, restarts it, confirms both resume correctly with distinct, non-colliding filings. 3 clean runs.
- [x] T3 S3 External Storage claim-check — labelled Preview. `app/temporal_client.py`'s `external_storage()`, using Temporal's own `S3StorageDriver` with a custom `_SyncBoto3S3Client` adapter (the SDK's suggested `aioboto3` driver conflicts with this project's `boto3` pin). Bucket created by the human 2026-08-29 per `docs/AWS_SETUP.md`. Threshold set to 64 KiB from measurement, not left at the 256 KiB default — the largest real payload here is the sandbox's ~84 KiB audit ledger, and inflating it past 256 KiB would have cost ~64K tokens per turn since it is fed to the model. Verified by new `make verify-external-storage`: **3 payloads / 594,883 bytes offloaded to S3, Event History 115,522 bytes — against 699,439 bytes for the same job shape before offloading**, plus a real bucket-object count check.
- [x] T4 Storage pane: payload size vs history size. `SessionState`'s `history_size_bytes`/`external_payload_size_bytes`/`external_payload_count`, straight off `WorkflowExecutionInfo` (no client-side arithmetic); Session Terminal renders the line only when a payload was actually offloaded, per the no-fake-data rule. Server half verified against the real API (`GET /api/jobs/{id}/state` returning all three fields for a job that offloaded).

**Also fixed while completing this story** (see `docs/DECISIONS.md`, 2026-08-29): `make replay` was not External-Storage-aware — its `Replayer` built a default converter with no S3 driver, so every history containing a claim-check reference failed with `[TMPRL1105]`, which reads like a determinism bug but is a config gap. Now reuses `client.data_converter`.

### Story E7.3 — Hosted lane

**Complete 2026-08-29, against a real deployed AgentCore Runtime.** See `docs/DECISIONS.md`'s 2026-08-29 E7.3 entries.

- [x] T1 `invoke_hosted_agent` activity against AgentCore Runtime — also owns **VendorCheck (E2.3 T4)**. `app/activities/hosted.py` (real `InvokeAgentRuntime`, shape confirmed against the installed boto3 service model; note the undocumented-in-prose 33-char `runtimeSessionId` minimum). `AgentJobWorkflow` gains its one branch, on `package.tier` — a manifest field, not an agent id, so it stays within CLAUDE.md §11. Hosted agent source at `infra/hosted/vendorcheck/main.py` (compliance screening — deliberately a different question from the Gateway's payment-risk lookup), package at `agents/vendorcheck/`. Runtime `VendorCheck_VendorCheck-8ZaMwNHpjY` deployed by the human via the `agentcore` CLI (CodeZip, no Docker), READY. **Verified:** direct `invoke_agent_runtime` smoke test returning correct verdicts on three vendors *before* any Temporal wiring; then `make verify-hosted` end to end — two jobs with opposite expected verdicts, each showing `invoke_hosted_agent` and **zero** `invoke_model*` activities (the tier-3 branch really does skip the native loop) with memory recall/write-back still wrapping the call; plus `dos agent test vendorcheck` on the on-disk package. `make verify-agents` clean (no regression from the `run()` refactor) and `make replay` clean at v16 over hosted-lane histories.
- [x] T2 Register a hosted agent by ARN from the store, no repo access — `dos agent register-hosted --runtime-arn ...` writes the registry row directly, no package on disk. The ARN lives in `AgentPackage.runtime_arn` (unlike the Gateway URL, which stays worker-side) precisely because it *is* the registration data. `dos agent validate` now rejects a tier-3 manifest declaring `tools`/`mcp_servers`/`approval_policy`/`output_model`, all of which are silently inert for a hosted agent — the `approval_policy` case being the dangerous one.
- [x] T3 Document the durability tradeoff: per-invocation, not per-turn — `docs/MULTI_AGENT.md` gains a hosted-lane section with the comparison table, what you still get (fair queueing, retries, memory, Event History, job-level resumption), and the ARN-not-a-repo install story.

### Story E7.4 — Multi-agent collaboration *(build, not stage)*

**Complete 2026-08-29. See `docs/DECISIONS.md`'s 2026-08-29 E7.4 entry and `docs/MULTI_AGENT.md`.**

- [x] T1 Strands Swarm handoff inside one job — invoice → fraud specialist. `app/activities/swarm.py`'s `investigate_fraud_swarm`: a real two-node `Swarm` (reviewer → `handoff_to_agent` → fraud specialist), run **entirely inside one Activity**, because `Swarm.__init__` calls `run_async` (which the workflow sandbox blocks) and Swarm's timeout bookkeeping reads real `time.time()` (a replay hazard) — both confirmed by reading Strands source. `invoice-exception` v3→v4; the SOP invokes it only when vendor risk is non-`low` **and** the amount exceeds the threshold, not on every invoice. Verified live: a $650 Initech Supply (medium-risk) invoice triggered the swarm, got `suspected_fraud=true`, and the agent correctly flipped settle → **hold**; a $60 low-risk invoice in the same regression run correctly never invoked it. `make verify-agents` clean, `make replay` clean at v15.
- [x] T2 `docs/MULTI_AGENT.md` — the inside-a-job vs between-jobs rule, why Swarm can't run in workflow code, the durability tradeoff table, and the `TemporalAgent`-pair alternative for when per-turn durability matters. *(Was T3; the old T2 cross-namespace task was dropped from the project on 2026-08-29.)*

---

## E8 — Demo hardening

### Story E8.1 — Rehearsal
**Acceptance:** full 8-minute run against real AWS, rehearsed end to end. No offline fallback (`docs/DECISIONS.md`, 2026-08-21) — venue needs internet/AWS access.

**New issue found 2026-09-01, should be fixed before the rehearsal — see `docs/DECISIONS.md`'s two entries.** The session terminal fails to stream later sessions on a long-lived page. **Three causes; two fixed, one open.** Fixed: (1) `EventSource` was never closed on `job_finished`, so every finished session reconnect-looped until the page starved into `net::ERR_INSUFFICIENT_RESOURCES` — closing it stopped that; (2) the same loop reached by attaching to an *already-terminal* job (a fast tier-1 job finishes inside the 2s job-poll interval, and an operator can select any past session), where `job_finished` never arrives at all — `onerror` now settles against real execution status via the polled-state endpoint. Open: on a later round the stream delivers a real session and then drops before `job_finished`, not yet root-caused. Also hardened Vite's dev proxy with `agent: false` after pooled upstream sockets from finished streams were seen blocking new proxied requests outright. This is directly on the stage path — the presenter runs several sessions in a row without reloading.

- [x] T0 **Root-caused and fixed 2026-09-04** (see `docs/DECISIONS.md`). Third cause was not a dropped stream at all: `App.tsx` re-picked the tenant's *newest* job every 2s and handed it to `SessionTerminal`, so a flood job arriving mid-session tore down a **live** `EventSource` (`readyState === 1`, `job_started` already delivered) and re-attached elsewhere — `job_finished` never arrived for the session the operator was watching. `pinnedJobId` now wins over auto-follow; the stream effect keys on `job?.job_id` rather than the object; selecting a tenant releases the pin. `.fixme` removed and the spec rewritten to start four *explicit* sessions with a flood underneath (the demo's real shape) and assert against `[data-job-id]`, because the old flood-driven version passed on a flood job's transcript while the defect was live. Proven both directions: fails on round 0 without the fix, 8/8 pass with it. No `BUILD_ID` bump — UI only.
- [x] T5 **Blocking boto3 in async activities starves the worker event loop** — found 2026-09-02, accepted not fixed at first; reopened and fixed 2026-09-04 (see `docs/DECISIONS.md`'s three 2026-09-02/09-04 entries) once the AgentCore-first restructure put the flood underneath every demo beat. Fixed on both the worker (`app/activities/registry.py`, `app/activities/memory.py`, `app/worker.py`'s `max_concurrent_activities=20`) and — found during this fix, not in the original scope — the identical pattern on the API (`app/registry/fleet.py`, `app/api/routes/metrics.py`, plus the UI's `setInterval` poll in `ui/src/App.tsx` compounding it into a full API wedge). No `BUILD_ID` bump. **Verified 2026-09-04:** `scripts/verify_flood_health.py` (new) 30/30 completed, 0 failed, 0 activities past attempt 1; `make replay` clean against 20 v18 histories; `make verify-payment` and `make verify-agents` (`scripts.verify_reference_agents`) both clean.
- [x] T1 **Mockoon collections for every external service — confirmed complete 2026-09-04.** One environment (`mocks/payment-service.json`, port 3001) is the whole set — grepped every `mockoon_base_url` call site (`app/activities/payment.py`, `app/activities/dispute.py`, `app/demo.py::payment_count`, plus the `scripts/verify_*` readers) and found nothing outside it; no mock invented for anything uncalled. Inventory written up in `mocks/README.md` (route ↔ call-site table).
- [ ] T2 `docs/DEMO_SCRIPT.md` — click-by-click, timed. **Now the 12-minute AgentCore-first run of show**, not the original 8-minute three-proof sequence — see `docs/superpowers/specs/2026-09-02-agentcore-first-demo-design.md`.
- [ ] T3 Full-run recording as in-window fallback (in case of AWS/network flakiness, not as an offline substitute)

### Story E8.2 — Laptop-hosted operation and the AgentCore-first demo path

**Added 2026-09-04, retroactively.** The demo was restructured to lead with AgentCore rather than with the three Temporal proofs (spec: `docs/superpowers/specs/2026-09-02-agentcore-first-demo-design.md`). That restructure created prerequisites which did not exist anywhere in this backlog while they were being built — this story is their home, and T1–T3 are recorded after the fact.

**Acceptance:** a presenter can take a cold laptop to `make preflight` → `make demo-prepare` → run every beat of the demo from the browser, without a terminal.

- [x] T1 **Start an agent session from the API** (commit `d51fba6`). Before this the UI could only trigger *floods*, whose agent is `incident-triage` — tier 1, deliberately toolless — so the only browser-launchable agent touched no AgentCore services at all, making an AgentCore-led demo impossible. New `POST /api/jobs` plus `app/sessions.py`, shared with `dos agent test`. Fixed a second defect on the way: `dos agent test` never wrote a `Job` row, so CLI-started sessions were invisible in the UI's session list, absent from p95, and missing from queued counts.
- [x] T2 **Run-session control in the UI** (commits `9d60e94`, `94f5b35`). Agent picker, prompt, Run. Landed in its own panel first; moved into the Session panel after measurement at 1920×1080 showed the extra pane clipped 288px of the Agent Store below the fold — a stage audience cannot scroll, and beat 3 depends on a newly registered agent being visible there.
- [x] T3 **Demo seed script** (commit `52b60f8`). `make demo-seed` runs two *real* `invoice-exception` sessions so beat 0's opening memory recall surfaces genuine prior decisions (INV-6742 settle, INV-6610 hold) instead of the near-identical `incident-triage` load-test summaries a rehearsal leaves behind. No fabricated memory rows — CLAUDE.md §7.
- [~] T4 **`make preflight`** — read-only readiness check for all eleven external dependencies plus demo state (fairness ON, kill switch disarmed, no ramp, payment counter 0, seeded memory present). Reports **remaining AWS SSO token lifetime**, not just validity: that token expired three times in a single development day, and it kills every beat at once because Bedrock, all four AgentCore services, DynamoDB and the sandbox share one credential chain. Its remediation text must say the worker needs restarting after `aws sso login`, since this app's boto3 sessions are `lru_cache`d.
- [ ] T5 **`docs/RUNBOOK.md`** — the four terminals, preflight, reset/seed, a failure-mode table, and an explicit statement of how the laptop topology differs from E9.1's documented AWS one.

**Known open items, all needed before E8.1 T2's rehearsal:**
1. **Agent Store clips 180px at 1920×1080** — the 5th agent falls below the fold. Beat 3 registers VendorCheck by ARN and the audience must *see* it appear, which would be a 6th. Load-bearing for that beat.
2. **Flood e2e spec is flaky** — fails in-suite, passes in isolation. Suspected attach race (a fast tier-1 job finishing before the pane attaches). Did not reproduce after E8.1 T0's fix; unexplained rather than resolved.
3. **API degrades under sustained flood backlog** — observed once *after* E8.1 T5's fix: two 500s on `/api/*` at page load and a 26-second first job poll. The new structure runs a flood underneath every beat, so this is the condition the whole show sits in.
- [x] T4 **Reset script — completed 2026-09-04.** `scripts/reset.py` (via `make demo-reset`) now returns all six dimensions to clean state: `Job`/`JobResult` rows, per-tenant AgentCore Memory events (`list_events`/`delete_event`, verified against the installed boto3 service model rather than guessed), fairness restored to ON, kill switch disarmed, an active Worker Deployment ramp cleared (`app/demo.py::clear_ramp`), and the Mockoon `payments`/`dispute-responses` buckets emptied. **Deviation from the original plan, verified empirically against the live `@mockoon/cli` 9.8.0 instance:** the buckets are cleared with `PUT .../<bucket>` body `[]`, not `DELETE` — Mockoon's CRUD route type's auto-generated bucket-level `DELETE` sets the databucket to `undefined` rather than `[]`, which breaks JSON parsing on the next `GET` (including `app/demo.py::payment_count`). `PUT` sets the databucket to exactly the request body, so no changes to `mocks/payment-service.json` were needed — see `mocks/README.md` and `scripts/reset.py::_clear_mockoon_bucket`'s docstring. Dry run by default, `--yes` to act, `--tenant` scopes the job-row/memory-event purge (fairness/kill-switch/ramp/Mockoon are global, always restored on `--yes`). Tenants read from the registry throughout, never hardcoded.

---

## E9 — Deploy

**Scope decision 2026-09-04:** E9.1 is **documented, not performed** — the SAM templates and the click-path go into `docs/AWS_SETUP.md` so a fork can deploy, but this repo's own demo is not deployed to AWS. The delivery lane is the laptop (E9.2). Anyone forking for a real deployment follows E9.1's written steps.

### Story E9.1 — AWS deployment *(document only — do not deploy)*
**Acceptance:** a reader can deploy a fork by following `docs/AWS_SETUP.md` alone. No resources are created by this repo's maintainers.

- [ ] T1 SAM template committed + steps written up: DynamoDB, IAM roles (Lambda worker, Temporal Cloud → Lambda invoke), S3
- [ ] T2 Steps for Workers as Serverless Workers on Lambda (the only deployed worker lane); Temporal Cloud auth via API key, per `docs/DECISIONS.md`
- [ ] T3 Steps for API + Mockoon on one App Runner service — one container, reverse-proxied so both the API and the Lambda worker can reach Mockoon
- [ ] T4 Steps for UI on Amplify Hosting
- [ ] T5 Mark the whole section clearly as untested-by-us in `docs/AWS_SETUP.md` — writing steps we have not run and implying we have would be dishonest to a forker

### Story E9.2 — LocalStack lane *(this is the one we actually build)*
As a presenter, I run the demo from my laptop with the commodity AWS services local, so a DynamoDB or S3 hiccup at the venue cannot break the show.

**Acceptance:** `make preflight` passes with `DynamoDB` and `S3` pointed at LocalStack; all three proofs still pass; `make replay` clean.

**Read this before starting — the honest scope.** LocalStack covers **DynamoDB and S3 only**. Everything else stays on real AWS, and not by preference:

| Service | Where it runs | Why |
|---|---|---|
| DynamoDB | LocalStack | registry, job index, idempotency, fairness/kill records |
| S3 | LocalStack | External Storage claim-check (Preview) |
| Bedrock (Claude, Nova) | **real AWS** | the models |
| AgentCore Memory / Gateway / Code Interpreter / Runtime | **real AWS** | LocalStack has no AgentCore support |
| Bedrock Guardrails | **real AWS** | ditto |
| `vendor-directory` Lambda | **real AWS** | it is invoked *by* AgentCore Gateway, an AWS-hosted service that cannot reach a laptop |
| Temporal Cloud | **real, remote** | CLAUDE.md §2 — never a local server |
| Mockoon | laptop | already local |

**So this does not remove the venue's internet dependency** — Bedrock, four AgentCore services and Temporal Cloud are all still remote. It reduces blast radius for two services; it is not an offline mode, and must not be described as one.

- [ ] T1 **Supersede the conflicting rules first.** CLAUDE.md §2's "Real AWS, not offline … no DynamoDB Local" and §5's "DynamoDB (real AWS, all environments)" both forbid this. Amend both and append a `docs/DECISIONS.md` entry superseding the 2026-08-21 decision, with the reasoning above. Do not write code before this — the repo must not contradict itself.
- [ ] T2 Choose Docker Desktop vs LocalStack Desktop and record why. Note that E0.1 T2 deliberately removed Docker Compose from this project; reintroducing a container runtime is a reversal that needs stating.
- [ ] T3 Endpoint-override plumbing: one place that decides the boto3 endpoint per service, driven by env (`AWS_ENDPOINT_URL_DYNAMODB`, `AWS_ENDPOINT_URL_S3`). Must **not** leak into the six AgentCore/Bedrock clients. Folds naturally into the shared `app/aws.py` session helper already noted as owed (2026-09-02) — the eight duplicated session blocks become one.
- [ ] T4 Temporal External Storage against LocalStack S3 — `_SyncBoto3S3Client` needs the endpoint override and probably `s3_force_path_style`. Verify with `make verify-external-storage`, which already asserts real byte counts.
- [ ] T5 Table + bucket creation on a fresh LocalStack: a script, since the human-run AWS Console click-path from `docs/AWS_SETUP.md` does not apply. This is the one place the "AWS actions are manual" rule (CLAUDE.md §2) legitimately does not bind, because nothing real is being created — say so in the script's docstring.
- [ ] T6 `make preflight` distinguishes LocalStack-backed from real endpoints and prints which is which, so nobody mistakes a passing preflight for a real-AWS check.
- [ ] T7 Re-verify all three proofs on the LocalStack lane, plus `make replay`. Proof 3 is the one at risk: idempotency lives in DynamoDB, so LocalStack's consistency behaviour is directly load-bearing on the payment counter reading exactly 1.
- [ ] T8 Document the split in `docs/RUNBOOK.md` — which services are local, which are remote, and the explicit statement that this is not an offline mode.

---

## E10 — Open source release

### Story E10.1 — Make it forkable
**Acceptance:** a stranger adds an agent by following the README alone.

- [x] T1 README: thesis, three proofs, Mermaid architecture + one-job sequence diagram, the stack (Temporal features / Strands / Bedrock / AgentCore each called out), repo layout, quickstart, agent-authoring tiers, every `make verify-*` entry point, epic status, and an explicit **known gaps** section. Both diagrams were rendered and checked, not just written; every `make` target and doc link in it was verified to exist.
- [x] T2 `CONTRIBUTING.md` — already written (with `agents/_template/`, E2.1 T4) and covers exactly this task: the three tiers, the tier-1 no-code loop, every manifest field, SOP placeholder rules, how to add a tier-2 tool idempotently, and a pre-PR checklist. Ticked 2026-09-01 after audit; README now links to it as the agent-authoring entry point.
- [x] T4 Label every preview feature with its status — one table in the README (Priority & Fairness, Worker Deployment Versioning, Serverless Workers, Workflow Streams, External Storage, `contrib.strands`), matching the labels already carried in code comments (`app/worker.py`, `app/config.py`, `app/temporal_client.py`, `app/workflows/agent_job.py`, `app/api/routes/events.py`) and CLAUDE.md §2. Nothing claims GA that is not GA.
- [x] T3 Worked tier-1 example (Returns Triage) end to end — `agents/returns_triage/`, authored by walking the documented loop exactly as a newcomer would (`agent-init` → edit two files → `agent-validate` → `agent-publish` → `tenant install` → `agent-test`), which is what makes it a test of the docs and not just of the runtime. Tier 1: a manifest and an SOP, no Python. New `make verify-returns-triage` runs six real jobs, one per SOP rule, and asserts the decision each rule requires — including the two cases where rules deliberately conflict (a faulty item above the auto-approve limit must still `refund`; a used item below it must still go to `inspect`) — plus **zero tool activities in the history**, so the tier-1 property is proven rather than declared.
- [ ] T5 Publish to Temporal Code Exchange — **to be completed**; blocked on E9 (nobody can deploy a fork yet) and on E8's demo script.

**Verified 2026-09-01:** `make verify-returns-triage` — two consecutive clean runs, 12 real jobs on Temporal Cloud against real Bedrock, all six rules correct both times including both rule-conflict cases. Confirmed the zero-UI-code claim live: `returns-triage` appears in the running Agent Store as `v1 · NATIVE · tier 1 · No-code · installed` for globex with nothing in `ui/` touched. Two real papercuts in the authoring path were found by walking it and fixed: `dos agent init` copied the template's own README into every new package (so a scaffolded agent introduced itself as "Agent package template ... Populated in E2.1" and referenced an `interventions` field that does not exist), and the template README itself was stale. `agent init` now writes a per-agent stub, and the template README describes the template.

**Note:** the README's "Known gaps — to be completed" section is the public-facing mirror of this backlog. If a gap is closed here, close it there too, or the README starts lying to people who fork the repo.
