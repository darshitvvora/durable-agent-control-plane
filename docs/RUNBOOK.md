# RUNBOOK — operating the demo from a laptop

**Audience:** whoever is driving the demo. Read it once end to end before the first rehearsal; on delivery day, work from §4 (pre-delivery timeline) and §6 (failure modes) alone.

**Scope.** This is the *operating* guide: which processes run where, what to do before walking on stage, and what to do when something breaks. It is **not** the run of show — the beat-by-beat narration lives in [docs/superpowers/specs/2026-09-02-agentcore-first-demo-design.md](superpowers/specs/2026-09-02-agentcore-first-demo-design.md), and the click-by-click timed script becomes [docs/DEMO_SCRIPT.md](DEMO_SCRIPT.md) once it has been rehearsed for real (E8.1 T2).

---

## 1. The delivery lane is a laptop

The demo is delivered from **one laptop**, with four processes running locally against real Temporal Cloud and real AWS. This is a deliberate scope decision, not a shortcut: E9.1's AWS deployment is *documented so a fork can deploy it*, but is not what we present from (`docs/BACKLOG.md` E9, 2026-09-04).

| Runs on the laptop | Runs remotely, always |
|---|---|
| Worker (`app.worker`) | Temporal Cloud — namespace, task queue, visibility, Worker Deployment Versions |
| API (FastAPI, the sole Temporal client) | Bedrock — Claude, Nova, Guardrails |
| UI (Vite dev server) | AgentCore — Memory, Gateway (MCP), Code Interpreter, Runtime |
| Mockoon (mocked payment + dispute services) | DynamoDB — registry, tenants, installs, job index, idempotency keys |
| | S3 — External Storage claim-check |

**There is no offline fallback.** Every number on screen comes from a real Temporal or AWS call (CLAUDE.md §7), so the venue needs working internet. The in-window fallback for a network failure is the recorded full run (E8.1 T3), not a local mode.

### How this differs from E9.1's AWS topology

Anyone reading `docs/AWS_SETUP.md` to deploy a fork gets a different shape. The differences that change *operating* behaviour:

| | Laptop (this runbook) | E9.1 AWS (documented, not used by us) |
|---|---|---|
| Worker | One local process, `make worker` | Serverless Workers on Lambda — the only deployed worker lane |
| Restarting the worker | You do it, by hand, in a terminal. **Proof 3 depends on this being a real process you can kill.** | Lambda re-invocation; there is no process to kill at a tool boundary |
| API + Mockoon | Two separate local processes on ports 8000 and 3001 | One App Runner service, one container, Mockoon reverse-proxied so the Lambda worker can also reach it |
| UI | Vite dev server on `localhost:5173` | Amplify Hosting |
| AWS credentials | Your SSO profile (`AWS_PROFILE`), with a token that **expires** | IAM execution roles — no token to refresh |
| Temporal auth | Cloud API key in `.env` | Cloud API key in Lambda config |

The two operationally load-bearing consequences: **the SSO token is a live failure mode for us and does not exist in the deployed lane** (§6), and **`make worker` being a real killable process is what makes proof 3 demonstrable at all**.

---

## 2. One-time setup

Do this once per machine. All of it is already done on the demo laptop.

1. **AWS resources** — follow [docs/AWS_SETUP.md](AWS_SETUP.md) end to end. It is append-only and covers every human-run AWS step (DynamoDB table, Guardrail, AgentCore Memory/Gateway/Runtime, S3 bucket, IAM), plus the one-time Temporal Cloud step: the **`TenantId` Keyword search attribute** on the namespace. Without that attribute the Process Monitor renders `?` instead of per-tenant counts.
2. **`.env`** — copy `sample.env` to `.env` and fill every key. `make preflight`'s first check fails loudly if anything demo-critical is empty. Watch the comment-on-its-own-line trap noted in `app/config.py`: `KEY=` followed by a `#` comment on the *same* line loads the comment as the value.
3. **Python deps** — `uv sync`. After moving or renaming the repo directory, `uv sync --reinstall` (the `dos` entrypoint is an editable install).
4. **UI deps** — `cd ui && npm install`.
5. **Mockoon CLI** — `npm install -g @mockoon/cli` (v9.8.0 is what the Mockoon behaviour in `scripts/reset.py` was verified against).
6. **Registry seeding** — tenants and agent packages live in DynamoDB, not in code. `make tenant-list` and `make agent-list` should both return rows; if not, `make tenant-add` / `make agent-publish ID=<id>` per `docs/AWS_SETUP.md`.

---

## 3. The four terminals

Start them in this order. Each stays running for the whole session; keep them on separate tabs, not backgrounded, so you can see a crash.

```bash
# 1 — Mockoon (mocked payment + dispute services), port 3001
make mockoon

# 2 — Worker: Temporal Cloud + Bedrock + AgentCore + DynamoDB
make worker

# 3 — API: the ONLY Temporal client, port 8000
make api

# 4 — UI: Vite dev server, http://localhost:5173
make ui
```

Keep a **fifth terminal free** for demo controls (`make preflight`, `make demo-flood`, `make demo-prepare`, and the worker restart in beat 1).

**Order matters in one place:** Mockoon before the worker. The worker's first payment or dispute call fails against a dead Mockoon, and while Temporal retries it cleanly, the retry is visible noise you don't want mid-beat.

**All four terminals come up before §4's reset-and-seed, not after.** `make demo-seed` starts two *real* workflows, so with no worker polling the task queue they sit at `WorkflowTaskScheduled` and the command simply hangs with no error — the same silent hang §6 lists for a stale deployment version, reached through setup ordering instead. Worse than a lost minute: the abandoned jobs keep accruing queue wait, and because p95 is windowed by sample count they stay in the window. One such job pinned acme's p95 at **923s** during E8.1 T2's rehearsal, which is proof 1's protected tenant. If a seed ever hangs, start the worker, then re-run `make demo-prepare` from the top rather than just `make demo-seed`.

**After any `aws sso login`, restart the worker _and_ the API.** Both cache their boto3 sessions with `lru_cache`, so either one started with a dead token keeps using it after you re-login. The API is easy to forget because the UI dev server stays up and the page still renders — it just answers 500 on every `/api/*` route, which reads like a broken UI rather than a credential problem (observed 2026-09-16). The UI and Mockoon need no restart; neither holds AWS credentials. This has bitten this project repeatedly — see §6.

---

## 4. Pre-delivery timeline

Work backwards from your slot. The whole sequence is under ten minutes, but `make demo-prepare` runs two real Bedrock sessions, so don't start it two minutes before you walk on.

### T−30 — refresh credentials, bring the stack up

```bash
aws sso login --profile <your AWS_PROFILE>
```

Then start the four terminals (§3). Because you just logged in, the worker is starting *after* the fresh token — which is the correct order.

### T−20 — confirm the worker's deployment version is current

A versioned worker receives **no** workflow tasks until its deployment version is set current. Skip this and every workflow sits at `WorkflowTaskScheduled` forever, with no error anywhere (CLAUDE.md §4).

```bash
temporal worker deployment set-current-version \
  --deployment-name agent-control-plane --build-id <BUILD_ID from .env>
```

`make preflight` checks this for you and prints the exact command if it's wrong. It only needs running when `BUILD_ID` has changed since the last delivery.

### T−15 — reset and seed

```bash
make demo-reset            # dry run first — prints exactly what it will delete
make demo-prepare          # reset --yes, then seed
# then restart Mockoon (Ctrl-C terminal 1, `make mockoon`) — see below
```

**Restart Mockoon after seeding.** Prepare necessarily ends with the payment counter at **1**, because seeding's `INV-6742` scenario settles for real; preflight expects **0** and will FAIL on an otherwise-ready stack. Restarting Mockoon zeroes its process-local payments bucket while leaving the seeded AgentCore Memory events untouched, which is what makes beat 1's "the counter still reads 1" line true. Never resolve this by re-running `demo-prepare`: that purges the seed and re-creates the payment.

`make demo-prepare` does two things, in order:

- **Reset** (`scripts/reset.py --yes`) returns six dimensions to clean: `Job`/`JobResult` rows, per-tenant AgentCore Memory events, fairness restored to **ON**, kill switch **disarmed**, any active version ramp **cleared**, and Mockoon's `payments` and `dispute-responses` buckets **emptied**. Tenants, agent packages, installs and idempotency records are fixtures and are never touched.
- **Seed** (`scripts/seed_demo.py`) runs **two real `invoice-exception` sessions** for `acme` (INV-6610 held, INV-6742 settled) so beat 0's opening memory recall surfaces genuine prior decisions instead of the near-identical `incident-triage` load-test summaries a rehearsal leaves behind. No fabricated rows (CLAUDE.md §7).

Why the reset is not optional between deliveries:

- **p95 is windowed by sample count**, not by time (the tenant's most recent 200 jobs). A previous round's fairness-OFF long waits stay in the window and flatten exactly the contrast proof 1 turns on.
- **Every flood job writes a memory event.** Left in place, beat 0's recall opens on load-test noise about an unrelated `checkout-api` incident.

### T−5 — preflight

```bash
make preflight
```

20 read-only checks — `.env`, AWS credentials **and remaining SSO token lifetime**, DynamoDB, Temporal Cloud reachability and the `TenantId` attribute, worker deployment version vs `BUILD_ID`, task-queue poller count, Mockoon's three route families, Bedrock models and guardrail, AgentCore Memory/Gateway/Runtime, S3, the API and UI dev servers, and five demo-state rows (fairness ON, kill switch disarmed, no ramp, payment counter 0, seeded memory present).

Nothing in it mutates state, so it is safe to run repeatedly, including seconds before you walk on. Every non-PASS row prints its own `fix:` line.

**Exit 0 with zero FAIL is the go/no-go gate.** WARN rows are judgement calls; the one you will see most is the SSO token dropping below its two-hour margin, which is a real signal — re-login and restart the worker and the API rather than hoping.

### T−2 — final credential check

```bash
make preflight   # again, for the SSO row alone
```

An expired SSO token kills Bedrock, all four AgentCore services, DynamoDB and the sandbox **simultaneously**, because they share one credential chain. It expired three times in a single development day, and again mid-rehearsal on 2026-09-16 roughly an hour after a preflight WARN reported the margin. If the token is anywhere near its margin, `aws sso login` and restart the worker and the API now — it costs 30 seconds here and the whole demo on stage. For a 13-minute run, walk on with hours of headroom, not minutes.

### T−0 — the cold open

Start the flood **while still on the intro slide**, before the UI is on screen:

```bash
make demo-flood TENANT=initech COUNT=200
```

The Process Monitor is then alive from the first second the UI appears, the flood runs underneath beats 0–3, and proof 1 later becomes *"this has been true the whole time — watch what happens when I turn it off."* The flood's agent is `incident-triage` (tier 1, deliberately toolless) so it generates queue pressure without competing for Bedrock or AgentCore capacity with the beats.

---

## 5. Operator actions, by beat

Only the actions that need a keystroke or a terminal. Narration is in the design spec.

| Beat | What you do | Where |
|---|---|---|
| Cold open | `make demo-flood TENANT=initech COUNT=200` | terminal 5, before the UI is shown |
| Establishing shot | Bedrock AgentCore console: Gateway `READY` + its `vendor-directory` target, Memory `ACTIVE`, Runtime `READY` | browser tab, pre-opened |
| 0 — an agent with hands | Pick `invoice-exception` + `acme`, paste the prompt, **Run** | UI Session panel's own run control |
| 0 — console moment | AgentCore Memory console, showing the event this session just wrote | browser tab |
| 1 — crash | **Arm at tool boundary**, run the session, wait for the worker process to exit, then `make worker` to restart | UI System Controls, then terminal 2 |
| 1 — the payoff | The Status Strip's **Payments** readout still reads **1** | UI Status Strip |
| 2 — sandbox + guardrail | Run `dispute-resolution` with the **exact rehearsed prompt** | UI Session panel |
| 3 — extensibility | `uv run dos agent register-hosted --runtime-arn <arn>`, then Install to a tenant in the UI, then Run | terminal 5, then UI |
| 4 — fairness | Toggle fairness **off**, watch protected tenants' p95 collapse, toggle back **on** | UI System Controls |
| 5 — versioning | Ramp new sessions toward the new build while sessions are in flight | UI System Controls → target build id + percent + **Ramp**, or `make demo-ramp VERSION=<build-id> PERCENT=<n>` |

Notes on the three beats with real operator hazards:

**Beat 1 — the worker really dies.** `dos demo kill-worker` (and the UI's Arm button) only *arms* a DynamoDB `KillSwitch` record; the worker then calls `os._exit(1)` right after its next payment or dispute call succeeds but before Temporal records completion. Nothing restarts it for you. Terminal 2 will show the process gone — bring it back with `make worker`. The counter reading exactly 1 afterwards is the proof; read it off the Status Strip, and `uv run dos demo payment-count` is the same number if you want it in the terminal.

**Beat 2 — the guardrail prompt is not improvisable.** Triggering a real block took five attempts during development, because the denied topic overlaps the model's own refusal boundary — the model declines first and the gate never sees a proposal (`docs/DECISIONS.md`, 2026-09-01). Use the exact prompt from `DEMO_SCRIPT.md` once the rehearsal has fixed it. Treat improvising here as a known failure mode.

**Beat 5 — "deploying v2" on a laptop means a second worker process.** The mechanism is proven end to end by `make verify-pinning`, which starts a second worker under its own build id (`BUILD_ID=<new> uv run python -m app.worker`), moves `current`, and shows an in-flight paused session finish on the build it started on while a new session runs on the new one. The exact on-stage sequence — which build is v1, which is v2, and whether the second worker is pre-started before the beat — comes out of the rehearsal and belongs in `DEMO_SCRIPT.md`. Do not improvise it live; a ramp is real routing config on the namespace, and `make demo-reset` is what clears it afterwards.

---

## 6. Failure modes

Ordered roughly by how likely you are to hit them.

| Symptom | Cause | Fix |
|---|---|---|
| Everything AWS-shaped fails at once — Bedrock, Memory, Gateway, sandbox, DynamoDB | **AWS SSO token expired.** One credential chain backs all of them | `aws sso login --profile <profile>`, then **restart the worker _and_ the API** — both `lru_cache` their boto3 sessions, so either keeps using the dead token |
| The page renders but every panel is empty or errors, and the UI itself seems fine | Same expired token, seen from the front end: the Vite dev server holds no credentials so it stays up, while the API answers 500 from DynamoDB | Check `make preflight`'s credentials row first, not the UI. Re-login, restart worker **and** API |
| Workflows start but sit forever at `WorkflowTaskScheduled`, no error anywhere | Worker deployment version is not `current` for this `BUILD_ID` | `temporal worker deployment set-current-version --deployment-name agent-control-plane --build-id <BUILD_ID>`. `make preflight` prints this |
| Per-tenant lanes render `?` instead of counts | `TenantId` search attribute missing on the namespace | One-time `tcld namespace search-attributes add` — see `docs/AWS_SETUP.md` |
| Payment or dispute activity retrying visibly | Mockoon not running, or the wrong collection loaded | `make mockoon`; `make preflight`'s Mockoon row checks all three route families |
| Payment counter reads **1** right after `make demo-prepare`, and preflight FAILs | Correct, not a fault: prepare is reset (bucket → 0) **then** seed, and seeding's `INV-6742` scenario settles for real | **Restart Mockoon** (`make mockoon`). The bucket is process-local in-memory state so it zeroes; AgentCore Memory is AWS-durable so the seeded recall survives. Do **not** follow preflight's own advice to re-run `demo-prepare` — that purges the seed and re-creates the payment |
| Payment counter reads some other unexpected number | Residual payments from an earlier round | Expected baseline before the show is **0**. Restart Mockoon, or `make demo-reset ARGS="--yes"` if you also want job rows and memory cleared (then re-seed) |
| Beat 0's memory recall surfaces `checkout-api` incident noise | Reset ran without a re-seed, or didn't run at all | `make demo-prepare` |
| Fairness ON and OFF look the same | Previous round's job rows still in the 200-sample p95 window | `make demo-reset ARGS="--yes"` between rounds |
| Worker exits unexpectedly at a tool call | Kill switch left **armed** from a previous run | `make demo-reset ARGS="--yes"` disarms it. `make preflight` flags it as a FAIL |
| New sessions land on an unexpected build | Ramp left active from a previous rehearsal | `uv run dos demo ramp --clear`, or `make demo-reset ARGS="--yes"`. `make preflight` flags it |
| `make preflight` shows 2 pollers | A just-restarted worker's predecessor is still within `describe_task_queue`'s recently-seen window | Wait ~5 minutes; if it persists, hunt the leftover process. Two live workers on different builds will route sessions unpredictably |
| Temporal unreachable but AWS is fine | Temporal Cloud API key expired — **independent** of the AWS SSO token | Check `TEMPORAL_CLOUD_API_KEY` in `.env` |
| The session pane goes quiet mid-session | Was root-caused and fixed (`docs/DECISIONS.md`, 2026-09-04) — the browser was tearing down its own live `EventSource` when the job poller re-targeted | If it recurs, reloading the page between beats costs ~3s and is survivable |
| API returns 500s / first job poll takes tens of seconds under a long-running flood | **Open item.** Observed once after the event-loop fix, under sustained flood backlog | Reduce the cold-open flood count; the whole show runs in this condition, so watch for it in rehearsal |

**Nuclear option, mid-show:** `make demo-reset ARGS="--yes"` restores fairness, disarms the kill switch and clears any ramp in one command. It also deletes job rows and memory events, so it costs you beat 0's seeded recall until you re-run `make demo-seed`.

---

## 7. After delivery

```bash
make demo-reset ARGS="--yes"
```

Leaves the stack ready for the next rehearsal. Re-run `make demo-seed` (or just `make demo-prepare` next time) before the following delivery.

If `BUILD_ID` changed during the session — for instance because beat 5 introduced a second build — check that `current` is back on the primary build before walking away, or the next rehearsal will start with workflows sitting at `WorkflowTaskScheduled`.

---

## 8. Verification you can run any time

These run against real Temporal Cloud and real AWS; there is no unit-test lane in this repo by design (CLAUDE.md §2). Each Makefile target's comment records which processes it needs.

```bash
make preflight            # read-only readiness — safe always, mutates nothing
make replay               # non-determinism guard, real recorded histories
make ci                   # ruff + mypy

make verify-kill-resume   # proof 3, end to end — spawns its OWN worker; stop `make worker` first
make verify-pinning       # proof 2's pinning mechanism — spawns a second worker alongside yours
make verify-tenant-priority   # proof 1
make verify-flood-health  # 30 real jobs; asserts zero failures and zero activity retries
```

`make verify-flood-health` is the one worth running after any change to activity or API code paths: an activity past attempt 1 under load is the signature of the event-loop starvation class of bug (`docs/DECISIONS.md`, 2026-09-04).

---

## 9. Known open items

Carried here from `docs/BACKLOG.md` E8.2 so they're in front of you during rehearsal rather than buried:

1. **Agent Store clips ~180px at 1920×1080** — the 5th agent falls below the fold, and beat 3 adds a 6th by design. A stage audience cannot scroll. Load-bearing for beat 3.
2. **Flood e2e spec is flaky in-suite**, passes in isolation. Suspected attach race. Unexplained rather than resolved.
3. **API degrades under sustained flood backlog** — one observation, after the event-loop fix. The restructured show runs a flood underneath every beat, so this is the condition the whole demo sits in.
4. **Proof 3's ten-run rehearsal (E6.2 T5)** — three clean automated runs exist; the presenter-driven repetitions are still open.
