# CLAUDE.md — Durable Agent Control Plane

Guidance for Claude Code working in this repository. Read this file fully before the first task of any session.

---

## 1. What this is

A multi-tenant **agent control plane** that demonstrates Temporal as the *outer durable harness* around Strands agents running on Amazon Bedrock and AgentCore.

**The thesis, which every design decision must serve:**

> Agents need an operating system. Strands is the inner harness — it runs the turn. Temporal is the outer durable harness — it runs the durable process the turns live inside. AWS supplies the silicon.

**One line:** Strands makes the agent think. Temporal makes the system trustworthy.

This ships as a live demo at AWS re:Invent 2026 (partner theater, 20 min) and afterwards as an open-source **boilerplate for durable agent control planes** — the reference agents (invoice exception, incident triage, dispute resolution, vendorcheck) are examples of what runs on it, not the point of it. The runtime — the generic workflow, the scheduler, the versioning machinery, the manifest-driven agent format — is the deliverable.

**This is designed to be extended, not just watched.** A new agent is a package under `agents/<id>/` (a `manifest.yaml` plus a `procedure.sop.md`), never a change to workflow or UI code — see §6 and the "Adding an agent" tiers below. That is the whole point of the "operating system" framing: anyone forking this repo to build their own agent control plane for a different domain should be able to install a new agent, tenant, or tool the same way, without touching the core.

Document link - https://app.notion.com/p/temporalio/Durable-Agent-Control-Plane-Durable-OS-ReInvent-2026-Demo-3c28fc56773880afa890f071ea9412b9?source=copy_link



You will use AWS skills for agentcore and strands and refer strands docs from here 

https://strandsagents.com/docs/user-guide/quickstart/python/
https://strandsagents.com/docs/user-guide/deploy/deploy_to_bedrock_agentcore/python/
https://strandsagents.com/docs/user-guide/deploy/deploy_to_aws_lambda/
https://strandsagents.com/docs/user-guide/deploy/deploy_to_aws_apprunner/
https://strandsagents.com/docs/user-guide/safety-security/guardrails/
https://strandsagents.com/docs/user-guide/safety-security/prompt-engineering/
https://strandsagents.com/docs/user-guide/observability-evaluation/observability/

### The three proofs — the demo's reason to exist

Everything built here must keep these three working. They are the acceptance test for the whole project.

1. **Fairness protects everyone else.** Fairness off → one tenant floods the queue → other tenants' p95 wait collapses. Fairness on → same flood → protected tenants hold steady.
2. **Safe version routing.** Deploy v2 with sessions mid-reasoning. In-flight sessions stay pinned to v1 and finish cleanly; new sessions start on v2.
3. **Kill and resume without duplication.** Kill the worker at a tool boundary right after a payment activity. Restart. Sessions resume at the same turn and the payment counter still reads exactly 1.

If a change would break, weaken, or obscure any of these three, stop and raise it before proceeding.

---

## 2. Non-negotiables

- **Never break determinism.** No I/O, no `random`, no `datetime.now()`, no non-deterministic iteration inside workflow code. All side effects go in Activities.
- **Idempotency is load-bearing.** The payment activity must be idempotent via a DynamoDB idempotency key. Proof 3 fails visibly if it is not.
- **One generic workflow.** `AgentJobWorkflow` serves every agent. Adding an agent must require **zero workflow code and zero UI code**. If a feature needs a second workflow type, raise it first.
- **State rule.** If state does not belong in Temporal event history, it goes in **DynamoDB**. No second relational store, no Redis, no S3-as-database.
- **Real AWS, not offline.** DynamoDB and the sandbox run against real AWS (no DynamoDB Local, no Docker sandbox fallback) from E0 onward. The demo requires internet/AWS access at the venue — there is no offline fallback path. (Superseded 2026-08-21; see `docs/DECISIONS.md`.)
- **Temporal Cloud only.** Never `temporal server start-dev`, never a local Temporal server, in any environment. Every workflow, every test, every dev session talks to the real Temporal Cloud namespace.
- **No unit tests in the repo.** Verification is done by running the real thing against real AWS and Temporal Cloud, via scripts under `scripts/` (`make verify`, `make replay`). Do not add `pytest`, a `tests/` directory, mocking libraries, or unit tests — the codebase stays clean of test scaffolding. The one guard that stays is `make replay`, which replays real recorded histories to catch non-determinism (see §2's determinism rule); it is not a unit test.
- **AWS actions are manual and documented.** Claude never runs AWS commands that create or mutate resources (tables, roles, functions, buckets, IAM policies, etc.) directly. Instead, write the exact steps for the human to run themselves — AWS Console click-path preferred, CLI as fallback — then append them to `docs/AWS_SETUP.md` so anyone forking the repo can reproduce the environment from README.md alone. Read-only checks (`describe-*`, `list-*`, `get-caller-identity`) are fine to run directly for verification.
- **Preview features carry the stage, but are labelled.** Proofs may rely on Public Preview features — proof 1 depends on Priority & Fairness, which is Public Preview and a paid Temporal Cloud add-on, so a GA-only rule would be unsatisfiable. Every preview feature used (Priority & Fairness, Serverless Workers, Workflow Streams, External Storage) **must** be labelled as Preview in code comments, in docs, and in the stage narration. Never claim GA for them. (Superseded 2026-08-21; see `docs/DECISIONS.md`.)
- **The API is the sole Temporal client.** The UI never talks to Temporal directly.

---

## 3. Skills and references

**Always use the `temporal-developer` skill** for any Temporal work. Read the relevant references before writing code:

- `references/python/python.md` — start here
- `references/core/determinism.md` + `references/python/determinism.md`
- `references/core/priority-fairness.md` — proof 1
- `references/core/versioning.md` + `references/python/versioning.md` — proof 2
- `references/core/ai-patterns.md` + `references/python/ai-patterns.md`
- `references/core/gotchas.md` before any debugging

**Strands integration — the skill does not cover it yet.** The skill's `references/integrations.md` catalog has OpenAI Agents SDK, Google ADK, LangGraph, LangSmith, and Spring AI, but **not** `temporalio.contrib.strands`. Do **not** infer the Strands API by analogy from the OpenAI plugin — they differ. Source of truth:

- `https://github.com/temporalio/sdk-python/blob/main/temporalio/contrib/strands/README.md`
- The installed package source in the venv

The package is marked **experimental**. Pin the version, and record any API drift in `docs/DECISIONS.md`.

**AWS skills** are used for Bedrock, AgentCore, Lambda, SAM, and DynamoDB work. If they are not installed, say so rather than guessing at AWS API shapes.

---

## 4. Known API shapes

Verified against the temporal-developer skill. Use these exactly.

**Priority and fairness (proof 1):**

```python
handle = await client.start_workflow(
    AgentJobWorkflow.run,
    job,
    id=job.id,
    task_queue=TASK_QUEUE,
    priority=Priority(
        priority_key=tenant.priority_key,
        fairness_key=tenant.id,
        fairness_weight=tenant.weight,
    ),
)
```

**Versioning (proof 2):** `AgentJobWorkflow` is `PINNED` — that is what keeps in-flight sessions on v1.

```python
@workflow.defn(versioning_behavior=VersioningBehavior.PINNED)
class AgentJobWorkflow: ...
```

Worker sets `default_versioning_behavior=VersioningBehavior.PINNED` in its deployment config. For long chat-style sessions using continue-as-new, check `workflow.info().is_target_worker_deployment_version_changed()` and continue-as-new with `ContinueAsNewVersioningBehavior.AUTO_UPGRADE`.

**Strands plugin:** attach `StrandsPlugin` to the **client**. The worker inherits it automatically — do **not** also pass `plugins=[...]` to `Worker(...)`, or the plugin's activities register twice and the worker dies at startup with `ValueError: More than one activity named invoke_model`. The plugin must reach the client (not just the worker) because the failure converter that carries `Interrupt` payloads across the activity boundary is installed via the client's data converter; a worker-only plugin silently breaks activity-tool interrupts. Verified 2026-08-21 against temporalio 1.31.0 — see `docs/DECISIONS.md`.

Use `agent.invoke_async(...)`, never `agent(...)` — the sync form spawns a thread the workflow sandbox blocks. Pass `callback_handler=None` so Strands does not print tokens to stdout (it fires on replay too).

**Worker Versioning operational gate:** a versioned worker receives **no** workflow tasks until its deployment version is set current:

```bash
temporal worker deployment set-current-version \
  --deployment-name agent-control-plane --build-id <build-id>
```

Skip this and workflows sit at `WorkflowTaskScheduled` forever with no error anywhere. This is also the mechanism proof 2's ramp control drives (E6.1).

**Bump `BUILD_ID` whenever workflow behaviour changes.** Editing what the workflow does while reusing a build id makes old and new code share one version identity, and `make replay` will (correctly) fail against the pre-change histories. `make replay` is scoped to the current build id because `AgentJobWorkflow` is PINNED — a run started on v1 is only ever replayed by v1 code.

**Workflow Streams (E4.1/E4.2, Experimental — `temporalio.contrib.workflow_streams`, ships in temporalio ≥1.27.0):** how `AgentJobWorkflow` gets live events to the API's SSE route. No custom event bus, no internal HTTP hop between worker and API — verified 2026-08-23 against temporalio 1.31.0 after being pointed at `github.com/temporalio/samples-python/tree/main/workflow_streams`; see `docs/DECISIONS.md` for what the first (wrong) attempt looked like.

```python
@workflow.defn(versioning_behavior=VersioningBehavior.PINNED)
class AgentJobWorkflow:
    @workflow.init
    def __init__(self, job: AgentJobInput) -> None:
        # Must match @workflow.run's params exactly. Construct the stream here,
        # not lazily in run(), so its handlers are registered before an
        # external subscriber can attach.
        self.stream = WorkflowStream()
        self.events = self.stream.topic("job_events", type=UIEvent)

    @workflow.run
    async def run(self, job: AgentJobInput) -> JobOutcome:
        self.events.publish(UIEvent(job_id=job.job_id, kind="job_started", payload={...}))
        ...
        # Hold the run open briefly so a subscriber's next poll delivers the
        # terminal event before the workflow closes and the log is gone.
        await workflow.sleep(timedelta(milliseconds=500))
```

Publishing straight from workflow code (`self.events.publish(...)`, synchronous, no activity, no try/except needed — it's an in-memory append, not I/O) is correct only when the workflow itself already knows the event's content. Event content that originates *inside* an activity (LLM token deltas, tool-call progress — E4.2's job) needs `WorkflowStreamClient.from_within_activity()` instead, publishing onto the same stream from the activity side; see `workflow_streams/activities/llm_activity.py` in the sample.

External subscribers (the API) reach in via `WorkflowStreamClient.create(client, job_id).subscribe([...], result_type=RawValue)`. This requires the workflow to already exist — subscribing before `start_workflow` returns raises `RPCError: workflow not found`, which is correct Temporal semantics, not a bug (a subscriber only ever has a `job_id` after the call that created it has already returned).

**Always check `github.com/temporalio/samples-python` before implementing a new Temporal-adjacent pattern**, in this repo or any fork of it — including this skill's own reference docs, which may lag. Verify the exact API against the *installed* `temporalio` version (`python -c "from temporalio.contrib.X import Y; import inspect; print(inspect.signature(...))"`) rather than trusting a sample file's version matches.

---

## 5. Stack

This stack is deliberately generic — one workflow type, manifest-driven agents, no domain logic baked into the core — so it works as a boilerplate for any team's agent control plane, not just this demo's four agents.

| Layer | Choice |
|---|---|
| Frontend | React + Vite + Tailwind + shadcn — Amplify Hosting |
| API | FastAPI, sole Temporal client, SSE bus — App Runner, same service as Mockoon (see below) |
| Workers | Serverless Workers on Lambda — the only worker lane, no Fargate |
| Orchestration | Temporal Cloud only — no local server, ever, Python SDK |
| Agent framework | Strands via `temporalio.contrib.strands` — the center of every activity: model calls, tool calls, and MCP calls all route through `StrandsPlugin` |
| Models | Amazon Bedrock — Claude, Nova |
| Tools | AgentCore Gateway (MCP) via `TemporalMCPClient` |
| Memory | AgentCore Memory, tenant-scoped |
| Identity | AgentCore Identity — per-tenant tool authorization; denial visible in the session pane |
| Guardrails | Bedrock Guardrails |
| Hosted agents | AgentCore Runtime (third-party lane) |
| Sandboxes | AgentCore Code Interpreter, invoked from a Temporal Activity |
| Large payloads | S3 via Temporal External Storage |
| App database | DynamoDB (real AWS, all environments) — registry, tenants, installs, job index, idempotency keys |
| Mocked services | Mockoon — same App Runner container/service as the API, reverse-proxied so it's reachable by the Lambda worker too |
| IaC | AWS SAM |
| Local dev | Temporal Cloud directly; Mockoon standalone; no Docker Compose |

**Default rule:** if state does not belong in Temporal event history, it goes in DynamoDB. No second relational store, no Redis. See `docs/DECISIONS.md` (2026-08-21, "What actually needs DynamoDB") for why each of the four DynamoDB uses can't just be workflow state.

**Hosting note:** the API is a request-driven HTTP service, so App Runner fits it. Workers have no inbound HTTP — App Runner would scale on requests that never arrive, so they run on Lambda instead.

**AgentCore usage map** — every AgentCore service considered, adopted or not, and why: see `docs/DECISIONS.md` (2026-08-21, "AgentCore usage map").

---

## 6. Repo layout

```
agents/                    # agent packages — the extensibility surface
  _template/
  invoice_exception/       # manifest.yaml, procedure.sop.md, tools.py
  incident_triage/
  dispute_resolution/
  vendorcheck/             # hosted lane — manifest only, no code
app/
  workflows/               # AgentJobWorkflow — the ONLY workflow type
  activities/              # model, tools, guardrail, sandbox, payment
  registry/                # manifest loader, agent package resolution
  api/                     # FastAPI, SSE bus, sole Temporal client
  worker.py
cli/                       # `dos` CLI — init, validate, test, publish
ui/                        # React desktop-OS control plane
infra/                     # SAM templates
mocks/                     # Mockoon collections
docs/
  BACKLOG.md               # epics, stories, tasks
  DECISIONS.md             # ADR log — append, never rewrite
  DEMO_SCRIPT.md           # the 8-minute run of show
  AWS_SETUP.md             # manual AWS steps, append-only, linked from README.md
```

---

## 7. UI direction — it must feel like a desktop OS

The UI is not a dashboard with an OS theme. It is an operating system for agents, and the resemblance should be structural, not decorative.

| Region | OS equivalent | Contents |
|---|---|---|
| Agent store | App store / package manager | Installable agents, per tenant, Install button, native vs hosted badge |
| Process monitor | `htop` | Per-tenant lanes, running/queued counts, p95 wait, priority tier |
| Session terminal | tty | Live token stream, tool calls, guardrail verdicts, version badge |
| System controls | Settings / task manager | Fairness toggle, version ramp slider, kill worker |
| Status strip | Menu bar | Worker count, sandbox count, S3 offloaded, version ramp state |

Rules:

- **Legible from ten metres.** Large type, high contrast, few colours. It is presented on a conference screen in a noisy hall.
- **State changes must be visible without narration** — lanes overtaking, badges flipping, a counter that does not move.
- **Four panes always on.** Every capability named in the abstract must be visible somewhere for the full 20 minutes, so no feature needs its own demo beat.
- **No fake data.** Every number on screen comes from a real Temporal or AWS call. A technical audience will notice a mockup.

---

## 8. How we work — AI-DLC, step by step

Work is decomposed **Epic → User Story → Task** and tracked in `docs/BACKLOG.md`. Follow the phase discipline below; this is an AI-first loop with explicit human approval gates.

### Inception — before any code

1. Restate the story in your own words, including what is explicitly out of scope.
2. List assumptions and open questions. **Ask them — do not assume.**
3. Propose acceptance criteria if the story lacks them.
4. **Gate: wait for human approval before writing code.**

### Construction — one story at a time

1. Read the relevant `temporal-developer` references first.
2. Propose the logical design: workflow/activity boundaries, data shapes, failure modes, determinism risks.
3. **Gate: confirm the design before implementing.**
4. Implement the smallest slice that satisfies the acceptance criteria.
5. Run it for real against Temporal Cloud and real AWS (never a local server — see §2). Verify the acceptance criteria yourself before reporting done.
6. Add or extend a script under `scripts/` so that verification is repeatable, not a one-off.
7. Append any non-obvious choice to `docs/DECISIONS.md`.

### Operations — after each epic

1. Full demo dry run end to end, against real AWS.
2. Re-verify all three proofs still hold.
3. Update `docs/DEMO_SCRIPT.md` if timings or clicks changed.

### Rules for the loop

- **One story at a time.** Do not batch stories to look productive.
- **Stop at gates.** Approval gates are not optional.
- **Surface bad news early.** If a story is not achievable as written, say so in Inception, not after three hours of code.
- **Never mark a task done you have not run.**

---

## 9. Definition of done

A story is done when all hold:

- [ ] Acceptance criteria demonstrably met, verified by running it for real
- [ ] Verification is repeatable via a `scripts/` entry point, not a one-off manual poke
- [ ] `make replay` passes for any workflow change — the non-determinism guard
- [ ] Runs against real AWS (DynamoDB, Code Interpreter sandbox); Mockoon reachable
- [ ] All three proofs still pass
- [ ] No new workflow type, no new datastore
- [ ] Decisions appended to `docs/DECISIONS.md`
- [ ] Any preview-feature usage labelled in code comments and docs

---

## 10. Commands

```bash
# dev loop — Temporal Cloud only, no local server (see §2)
mockoon-cli start --data mocks/<collection>.json   # already running standalone, no Docker Compose
uv run python -m app.worker            # worker — talks to Temporal Cloud, real AWS DynamoDB, Lambda sandbox
uv run uvicorn app.api.main:app --reload
cd ui && npm run dev

# agent packages — `dos` is the editable-install entrypoint (`uv run dos ...`);
# the make targets wrap it
make agent-list
make agent-init ID=<id>
make agent-validate ID=<id>
make agent-test ID=<id> PROMPT="..."
make agent-publish ID=<id>

# demo controls
dos demo flood --tenant initech --count 200
dos demo fairness --off | --on
dos demo ramp --version v2 --percent 100
dos demo kill-worker --at-tool-boundary

# verification — real AWS, real Temporal Cloud, no unit-test scaffolding in the repo
make verify                            # registry round-trip against the real DynamoDB table
make replay                            # non-determinism guard: replays real histories from Temporal Cloud
make ci                                # lint + typecheck
```

---

## 11. Do not

- Do not add a second workflow type without raising it first.
- Do not put agent-specific logic in `AgentJobWorkflow` — it belongs in the agent package.
- Do not call Temporal from the UI. The API is the only client.
- Do not introduce Redis, Postgres, or a message broker.
- Do not make a preview feature load-bearing for a stage proof.
- Do not hardcode tenant or agent lists — they come from the registry.
- Do not add dependencies without saying why in `docs/DECISIONS.md`.
- Do not write code during Inception.
- Do not guess at Strands or AWS API shapes. Read the source or say you cannot verify.
