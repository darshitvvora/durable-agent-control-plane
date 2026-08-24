# Decisions — ADR log

Append-only. Never rewrite a past entry — if a decision changes, add a new entry that supersedes it.

---

## 2026-08-21 — Temporal Cloud auth via API key, not mTLS

Client and worker authenticate to Temporal Cloud with an API key (`TEMPORAL_CLOUD_API_KEY`), not client cert/key. Simpler local setup for the demo; no cert rotation to manage.

## 2026-08-21 — Mockoon: same container image locally and on AWS

Local: `mockoon/cli` container via Docker Compose (Docker is installed locally; keeps parity with the other Dockerized services — DynamoDB Local, sandbox — rather than mixing in a native binary).

AWS (if the demo runs from cloud infra instead of a laptop): the same `mockoon/cli` image as an ECS Fargate task in the same cluster/VPC as the workers, reachable only via internal service discovery (Cloud Map/Service Connect) — not App Runner, since App Runner is public-facing by default and would need a VPC connector bolted on just to be reachable from the workers' private subnet. `MOCKOON_BASE_URL` swaps from `localhost` to the internal DNS name via the env-driven config module.

## 2026-08-21 — docs/BACKLOG.md location

`BACKLOG.md` was created at the repo root; moved into `docs/` to match CLAUDE.md §6.

## 2026-08-21 — Offline requirement dropped; real AWS from E0 onward

**Supersedes** the "Mockoon via Docker Compose locally" half of the entry above, and CLAUDE.md §2's former "Everything runs offline" non-negotiable.

- DynamoDB: real AWS DynamoDB in all environments. No DynamoDB Local.
- Sandbox: real Lambda MicroVMs in all environments. No Docker sandbox fallback.
- Mockoon: run standalone locally via `mockoon-cli` (already running on this machine), not via Docker Compose. AWS hosting for Mockoon (ECS Fargate, internal-only via Cloud Map/Service Connect, same VPC as workers — see the superseded entry above for the reasoning) still applies if the stack runs from cloud infra instead of a laptop.

Net effect: the demo requires internet/AWS access at the venue; there is no offline fallback path. CLAUDE.md §2/§9/§10 updated to match.

## 2026-08-21 — Serverless worker on Lambda grounded in `temporalio/samples-python`

Confirmed API shapes against `https://docs.temporal.io/develop/python/workers/serverless-workers/aws-lambda` and `github.com/temporalio/samples-python/tree/main/lambda_worker` (not a branch — the sample lives on `main`) rather than guessing:

- Handler: `run_worker(WorkerDeploymentVersion(deployment_name=..., build_id=...), configure)` from `temporalio.contrib.aws.lambda_worker`.
- `configure(config: LambdaWorkerConfig)` sets `task_queue`, `workflows`, `activities` on `config.worker_config`.
- Every workflow needs a `versioning_behavior` (`PINNED` or `AUTO_UPGRADE`) — Worker Versioning is required for Lambda deployment, not optional.
- Client config (namespace, address, auth) resolves from `TEMPORAL_CONFIG_FILE` env var → `temporal.toml` in `$LAMBDA_TASK_ROOT` → `temporal.toml` in CWD. The sample's `temporal.toml` uses mTLS; we use API key auth instead (see the entry above) — `ClientConfig.load_client_connect_config()` supports both.
- Marked **Public Preview** — matches CLAUDE.md §2's existing preview-feature list (Serverless Workers was already there) and the stack table's "Serverless Workers on Lambda (demo lane)" / "Lambda MicroVMs" rows. Not a new preview-feature exposure, just confirming the real API before E9 implementation.
- Deploy path in the sample: `uv pip install --target package --python-platform x86_64-unknown-linux-gnu` the `temporalio[lambda-worker-otel]` wheel, zip with app code + `temporal.toml`, `aws lambda update-function-code`. IAM role for Temporal Cloud → Lambda invocation via a CloudFormation template (`mk-iam-role.sh`).

## 2026-08-21 — Workers are Lambda-only; Fargate dropped

**Supersedes** the "Fargate (primary), Serverless Workers on Lambda (demo lane)" split in CLAUDE.md §5. All Temporal worker code runs as a Serverless Worker on Lambda in every environment — no Fargate worker fleet. Simpler to operate (one deploy path, one scaling model) and keeps the whole stack serverless end to end (App Runner + Amplify + Lambda). Worker Versioning stays required either way (it already was, for proof 2), so this doesn't add a new constraint.

## 2026-08-21 — Mockoon co-located with the API on App Runner

**Supersedes** the "Mockoon as its own Fargate task, internal-only via Cloud Map" half of the 2026-08-21 "same container image locally and on AWS" entry above.

Mockoon runs in the **same container, same App Runner service** as the FastAPI API — one image, one deploy. The container's entrypoint starts `mockoon-cli` on an internal port (e.g. `3001`) alongside `uvicorn` on the App Runner-exposed port; a reverse-proxy route in FastAPI (or a tiny Nginx/Caddy sidecar process in the same container) forwards `/mock/*` to the internal Mockoon port.

**Why the proxy, not just localhost:** the API isn't the only caller — Activities running on the Lambda worker also call the mocked third-party services directly, and Lambda can't reach the API container's `localhost`. Exposing Mockoon under a path on the same public App Runner URL (`https://<apprunner-url>/mock`) gives both the API and the Lambda worker one reachable `MOCKOON_BASE_URL`, without a second compute resource (no separate Fargate task, no Cloud Map).

## 2026-08-21 — What actually needs DynamoDB

Per-session/per-job state (conversation turns, in-progress tool calls, the current reasoning state of one `AgentJobWorkflow` run) is **not** in DynamoDB — it's Temporal workflow state, replayed from event history for free. That's the whole point of running the Strands loop inside the workflow.

DynamoDB (§5 "App database") holds exactly four things, each because it's read/written by things *other than* the one workflow execution it's about:

1. **Idempotency keys** — the one case Temporal genuinely cannot substitute for. Temporal guarantees the *activity function* is retried until it reports success; it does not guarantee the *external side effect* inside that function happens once. If the worker crashes after the payment activity's HTTP call succeeds but before Temporal records `ActivityTaskCompleted`, the activity is retried and the function runs again — a fresh invocation with no memory of the first attempt except what a durable, externally-checkable dedupe key gives it. This is proof 3's entire mechanism; it has to live outside workflow history.
2. **Registry (tenants, agent manifests, priority/fairness config)** — admin-mutated config read by many unrelated workflow executions and by the API/UI directly (agent store, tenant list) without touching Temporal. A workflow can't be "the" storage for config other workflows need to read.
3. **Installs** — same reasoning as registry: an admin-mutated join table (which tenant has which agent), not scoped to any one job.
4. **Job index** — a denormalized, queryable list for the process-monitor/session-terminal UI (per-tenant lanes, running/queued counts, p95 wait). This is the one case where Temporal *could* partially cover it — List Workflow Executions + search attributes — but a live, filtered, multi-tenant dashboard is more naturally served by a purpose-built index than by treating Temporal's visibility store as a query API. Kept in DynamoDB for now; revisit if visibility-API latency/limits are ever a problem.

## 2026-08-21 — AgentCore usage map

Every AgentCore service, and whether this project uses it:

| Service | Status | Why |
|---|---|---|
| **Gateway** | Adopted | MCP tool catalog, via `TemporalMCPClient` — already central to the design |
| **Memory** | Adopted | Tenant-scoped recall across turns/sessions |
| **Identity** | Adopted | Per-tenant tool authorization — one tenant's agent can't call another's scoped tools; denial visible in the session pane |
| **Runtime** | Adopted | Tier-3 hosted-agent lane (VendorCheck) — third-party agent code runs in its own address space, invoked from a Temporal Activity |
| **Runtime Container** | Adopted (tier 3 only) | ARM64 container build for the VendorCheck Runtime deployment |
| **Code Interpreter** | Adopted | Replaces the hand-rolled Lambda MicroVM sandbox lane (E7.2) — AWS's own managed Firecracker-microVM sandbox, invoked from a Temporal Activity like any other tool call. Confirm the actual API against AWS docs before implementing E7.2 (CLAUDE.md §2 — don't guess AWS API shapes). |
| **Policy** | Declined for now | Cedar/NL rules intercepting Gateway tool calls could add defense-in-depth tool authorization, but our human-in-the-loop and approval logic is deliberately inside the Temporal workflow (`BeforeToolCallEvent` interrupt) so every check is a Temporal event with full audit provenance — moving any of that into Policy would move it outside that audit trail. Revisit only as an additional coarse-grained layer underneath Identity, not a replacement. |
| **Harness** | Rejected | Harness is AWS's own config-only, no-code managed agent loop — but it runs *without* an outer durable harness: no fairness, no crash-safe resume, no safe versioning. Adopting it for tier-1 agent authoring would forfeit exactly the guarantees this project exists to prove. Our tier-1 path (`manifest.yaml` + `procedure.sop.md` running through `AgentJobWorkflow`) is the intentional alternative — it inherits the outer harness for free, which Harness alone cannot provide. |
| **Registry** | Rejected | AgentCore's own agent/tool/MCP catalog would duplicate our manifest-driven `agents/` registry, and coupling the boilerplate's install path to an AWS-specific catalog service undermines the "fork this for any domain, on any infra" pitch. Our DynamoDB registry stays the source of truth. |
| **Evaluations** | Rejected | The project's own stated scope explicitly excludes agent evaluation methodology (CLAUDE.md §1's "explicitly out of scope" list) — adding it would contradict that stance, not just be extra work. |
| **Payments** | Not in initial scope | Interesting fit for a future extension (e.g. VendorCheck paying a metered third-party risk API via x402), but the proof-3 payment activity is a mocked business payment, not an agent-pays-for-a-tool-call scenario — different problem. Revisit only if a reference agent needs it. |
| **Browser** | Not in initial scope | No current reference agent needs web automation. Candidate for a future worked example (E10.1's Returns Triage or similar), not core scope. |
| **Observability** | Not in initial scope | Temporal's event history already gives full outer-harness tracing; AgentCore Observability would add inner-harness (model/tool-level) spans on top. Worth adding later to power the session terminal's tool-call/guardrail-verdict display if Workflow Streams alone isn't enough — not needed to start. |

## 2026-08-21 — Config module: pydantic-settings, blank env vars are unset

`app/config.py` is the single place that reads env vars — everything else imports `get_settings()`. Used `pydantic-settings` (fail-fast validation, `.env` support out of the box) over hand-rolled `os.environ` parsing.

Gotcha worth recording: a blank line in `.env` (`BEDROCK_GUARDRAIL_ID=`, for a service not provisioned yet) loads as `""`, not unset — `Optional[str] = None` fields would silently hold an empty string instead of `None`, breaking any later `is None` check. Fixed with a `model_validator(mode="before")` that maps `""` → `None` before field validation runs, so required fields left blank now fail loudly (as they should) and optional fields left blank actually read as `None`. Covered by `tests/test_config.py::test_blank_env_var_is_treated_as_unset`.

## 2026-08-21 — Lint/typecheck/CI: ruff + mypy, GitHub Actions

`ruff` for lint (fast, single tool covers what flake8+isort+pyupgrade would separately) and `mypy` for type-checking (mature, works well with Pydantic v2 via `pydantic.mypy`). `Makefile` wraps the CLAUDE.md §10 commands (including ones that don't exist yet — `dos`, `ui/`, `app/api/main.py` — as forward declarations for the epics that build them) so local dev and CI call the exact same targets, no drift. `.github/workflows/ci.yml` runs `ruff check`, `mypy app cli`, `pytest` on push/PR to `main` via `astral-sh/setup-uv` — inert until the repo has a remote, but ready the moment it does.

## 2026-08-21 — DynamoDB single-table schema

One table (`agent-control-plane`), on-demand billing (no capacity planning — fits a spiky flood-demo workload, low-maintenance for forkers), one GSI:

| Item | pk | sk | Purpose |
|---|---|---|---|
| Tenant | `TENANT#<tenant_id>` | `TENANT#<tenant_id>` | priority_key, fairness_weight, tier, memory_namespace, s3_prefix, installed_agent_ids |
| AgentPackage | `AGENT#<agent_id>` | `VERSION#<version>` | manifest metadata, versioned — mirrors `manifest.yaml` |
| Job | `JOB#<job_id>` | `JOB#<job_id>` | tenant_id, agent_id, workflow_id, status, priority tier, timestamps — the "job index" |
| JobResult | `JOB#<job_id>` | `RESULT` | co-located with its Job (single-partition read); output payload or an S3 pointer if large |
| IdempotencyRecord | `IDEMPOTENCY#<key>` | `IDEMPOTENCY#<key>` | proof-3 mechanism; `ttl` attribute for automatic expiry |

`gsi1` (`gsi1_pk=tenant_id`, `gsi1_sk=created_at`) lets the process-monitor list a tenant's jobs newest-first without a table scan.

No separate "Install" item — a tenant's installed-agent set is small and always read together with the tenant, so it's a list attribute on Tenant rather than a join table.

Domain Pydantic models (`app/registry/models.py`) stay free of DynamoDB concerns (no `pk`/`sk` fields) — key construction and item marshalling live in the repository layer (`app/registry/repository.py`), so swapping the storage backend later wouldn't touch the domain models.

Table created manually per `docs/AWS_SETUP.md` (console, per CLAUDE.md §2's manual-AWS-actions rule) — not by a script Claude runs.

## 2026-08-21 — No unit tests; verification is scripts against real infrastructure

**Supersedes** an earlier same-day entry that proposed `moto`-based repository unit tests (that approach was reverted before it landed; `pytest` and `moto` were removed from the dependency list).

The repo carries no unit tests, no `tests/` directory, no `pytest`, no mocking libraries. Verification is done by running the real thing against real AWS and real Temporal Cloud, via scripts under `scripts/` invoked through the `Makefile`:

- `make verify` → `scripts/verify.py`: writes/reads/deletes one of every item type against the live DynamoDB table under a `verify-` prefix.
- `make replay` → `scripts/replay.py` (lands with E1.1): pulls real recorded histories from Temporal Cloud and replays them against current workflow code. This is the **one** guard that stays, because it's the enforcement mechanism for §2's determinism non-negotiable and for proof 3 — and it's not a unit test: it replays real production histories, which mocks by definition cannot.
- `make ci` → lint + typecheck only.

Rationale: the deliverable is a boilerplate people read and fork, so the codebase stays free of test scaffolding; and the three proofs are only meaningful when demonstrated against real infrastructure — a passing mock-backed test would prove nothing about fairness, version pinning, or exactly-once payment. CLAUDE.md §2/§8/§9/§10 updated to match.

## 2026-08-21 — Verified `temporalio.contrib.strands` API (v1.31.0, experimental)

Read from the installed package source, not inferred (CLAUDE.md §3). Exports: `StrandsPlugin`, `TemporalAgent`, `TemporalMCPClient`, `workflow`.

```
TemporalAgent(*, model: str | None, task_queue, schedule_to_close_timeout,
    schedule_to_start_timeout, start_to_close_timeout, heartbeat_timeout,
    retry_policy, cancellation_type, versioning_intent, summary,
    priority: Priority, streaming_topic: str | None,
    streaming_batch_interval: timedelta = 100ms, **agent_kwargs)

StrandsPlugin(*, models: dict[str, Callable[[], Model]] | None,
    mcp_clients: dict[str, Callable[[], MCPClient]] | None,
    mcp_connection_idle_timeout: timedelta | None)

TemporalMCPClient(server: str, *, cache_tools: bool = False, task_queue,
    ...same activity options..., priority: Priority)

strands.workflow.activity_as_tool(activity_fn, *, ...activity options...,
    activity_id, priority) -> AgentTool
strands.workflow.activity_as_hook(...)
```

Key facts confirmed:

- **`TemporalAgent` takes `priority`** — model/tool activities can carry the tenant's fairness key explicitly. Activities otherwise inherit the parent workflow's priority.
- `**agent_kwargs` passes through to the Strands `Agent`: `system_prompt`, `tools`, `hooks`, `messages`, `structured_output_model`.
- Use `agent.invoke_async(...)`, **never** `agent(...)` — the sync form spawns a worker thread, which the workflow sandbox blocks.
- `TemporalAgent` disables Strands' `ModelRetryStrategy`; passing `retry_strategy=` raises `ValueError`. All retries go through Temporal `retry_policy`.
- `take_snapshot()` / `load_snapshot()` raise `NotImplementedError` by design — event history already persists state at finer granularity.
- Plugin defaults the data converter to `pydantic_data_converter`, so our Pydantic models cross the activity boundary as-is.
- `StrandsPlugin` must be on **both client and worker** — the failure converter that preserves `Interrupt` payloads across the activity boundary is installed via the client's data converter. Without it, activity-tool interrupts silently do not work.
- Omitting `models=` registers a single `BedrockModel()` factory under the name `"bedrock"`.
- `temporalio.contrib.aws.lambda_worker` (`run_worker`, `LambdaWorkerConfig`) ships in 1.31.0 — the `[lambda-worker-otel]` extra is only needed for OTel.

## 2026-08-21 — Priority & Fairness constraints that shape the demo

From the `temporal-developer` skill's `core/priority-fairness.md` and the Temporal SA design-patterns catalogue:

1. **Public Preview, and Fairness is a paid Temporal Cloud feature**, enabled per-namespace in the Cloud UI. This made CLAUDE.md §2's original "proofs use only GA features" rule unsatisfiable, since proof 1 *is* fairness. §2 amended: preview features may carry a proof but must be labelled Preview everywhere, including stage narration.
2. **`priority_key` is an int 1–5**, lower = higher priority, default 3. Not an arbitrary integer — `Job.priority_key` and `Tenant.priority_key` must be constrained to that range.
3. **Fairness is not guaranteed across Worker Versions.** Proof 2 deliberately runs two versions at once, so proofs 1 and 2 must not overlap on stage — v1 must fully drain before the fairness beat. This is a `docs/DEMO_SCRIPT.md` ordering constraint, not just a nice-to-have.
4. **Task Queue partitioning interferes with fair dispatch**; single-partition config requires a Temporal Support request. Needed for the p95 divergence to be legible from ten metres (§7). Lead-time item — file early.
5. Fairness applies **at schedule time**, so enabling it mid-flood does not reorder an existing backlog. The demo's off→on toggle must therefore flood *again* after toggling, which the run-of-show already does.
6. Fairness does not cap absolute throughput of one key; per-key limits need `fairness-key-rps-limit-default`. Set separately for workflow vs activity queue types.

## 2026-08-21 — E1.1: manifest resolved by an activity, not passed in workflow input

The workflow reads the agent package from the registry via an activity at start, rather than the API snapshotting the manifest into the workflow input. Replay-safe either way (the activity result lands in event history), but this keeps the registry the single source of truth and keeps the API's start path thin. Cost is one activity round-trip per job.

## 2026-08-21 — E1.1 findings from running it for real

Three things only surfaced by actually running against Temporal Cloud + Bedrock, not from docs:

1. **Do not pass `StrandsPlugin` to both `Client.connect(plugins=...)` and `Worker(plugins=...)`.** The worker inherits plugins from its client; passing it twice registers the plugin's model activity twice and the worker dies at construction with `ValueError: More than one activity named invoke_model` (plus a `UserWarning` about the duplicate plugin). CLAUDE.md §4 previously said "attach to both the client and the worker", which is true as *intent* (the client is what installs the interrupt-preserving failure converter) but wrong as literal code. §4 corrected.

2. **A versioned worker gets no tasks until its deployment version is set current.** With `use_worker_versioning=True`, the worker registers the deployment on first poll but `CurrentVersionBuildID` stays empty, and new workflows sit at `WorkflowTaskScheduled` indefinitely — no error on the worker, no failure on the workflow, nothing in the history past event 2. Fix:
   `temporal worker deployment set-current-version --deployment-name agent-control-plane --build-id v1`
   This is the same API proof 2's ramp control drives, so E6.1 T2 must wrap it rather than treating it as one-off setup.

3. **`callback_handler=None` on `TemporalAgent`.** Strands' default handler prints tokens to stdout, and it fires during *replay* too, so `make replay` output was polluted with model text. Token output belongs on the session terminal via Workflow Streams (E4.2).

Also corrected: real Bedrock model IDs in this account are `anthropic.claude-sonnet-5` / `amazon.nova-pro-v1:0`, and on-demand invocation needs the cross-region **inference profile** ids `us.anthropic.claude-sonnet-5` / `us.amazon.nova-pro-v1:0`. `.env` and `sample.env` had a fabricated `anthropic.claude-sonnet-5-v1:0`. Verified callable with `aws bedrock-runtime converse`. Model ids must always be checked with `aws bedrock list-foundation-models` / `list-inference-profiles`, never assumed.

Sandbox: `SandboxRestrictions.default.with_passthrough_modules("strands", "boto3")` — both are imported inside workflow code paths and are not deterministic-safe to re-import per replay.

Verified end to end: `make verify-agent` starts a real `AgentJobWorkflow` on Temporal Cloud with a tenant `Priority`, the Strands loop calls real Bedrock Claude Sonnet 5, returns a typed `JobOutcome`, and `describe()` reports `VERSIONING_BEHAVIOR_PINNED` on `agent-control-plane.v1` — proof 2's pinning mechanism confirmed working. `make replay` replays the real histories with no non-determinism.

## 2026-08-21 — Playwright for end-to-end testing, once the UI exists

E2E verification after E5 (Desktop OS UI) is done with Playwright driving the real UI against the real stack — not unit tests (see the "No unit tests" entry above; Playwright is consistent with it, since it exercises the real system rather than mocking it). Scope: the three proofs as browser-driven runs, so a demo regression is caught before stage rather than during. Lands as `scripts/e2e/` invoked from the `Makefile`; not added until there is a UI to drive.

## 2026-08-21 — E1.2: idempotency keyed on `activity_id`, and what running it exposed

**Key derivation** (proof 3's mechanism): `payment:{workflow_id}:{activity_id}`, derived *inside* the activity from `activity.info()`.

`activity_id` is assigned when the activity is scheduled and stays fixed while `attempt` increments across retries — confirmed empirically, not just from the field names: `scripts/verify_payment.py` runs the activity twice with the same `activity_id` and different `attempt`, and asserts the provider's invocation count does not move. A distinct `activity_id` does reach the provider, so legitimate second payments are not swallowed. Deriving the key inside the activity is what makes it survive a crash; a key generated per invocation would differ on every retry and dedupe nothing.

**Ordering and the ambiguous window:** `claim` → call provider (passing the key as an `Idempotency-Key` header) → `complete`. If the worker dies between the provider call and `complete`, the retry sees a `pending` record and cannot know whether the money moved — so the key also goes downstream, which is how real providers (Stripe et al.) dedupe. Belt and braces: the DynamoDB record short-circuits the common case, the header covers the gap.

**The proof-3 path specifically:** the demo kills right after the activity function returns, so the record is already `completed`; the retry short-circuits and the provider is never called twice.

**Counter source:** the Mockoon mock deliberately does *not* dedupe — it records every call that reaches it, so the on-stage counter measures what actually hit the provider rather than our own bookkeeping. A skeptic can't say we counted our intentions.

Two real bugs found by running it, which no amount of reading would have caught:

1. **DynamoDB rejects floats nested in maps.** `_to_decimal` only converted top-level values, so writing a payment result (`amount_usd: float` inside the `result` map) raised `TypeError: Float types are not supported`. Both `_to_decimal` and `_from_decimal` are now recursive over dicts and lists.
2. **`PaymentResult(**record.result, deduplicated=True)` raised `got multiple values`** — the stored dict already contained `deduplicated`. Now stored with `exclude={"deduplicated"}` (it describes how a caller obtained the result, not the payment) and reconstructed via dict merge so old records stay readable.

**Tool wiring:** `app/activities/catalog.py` maps manifest tool names → activities with per-*class* activity options — consequential tools (move money) get few attempts and must be idempotent; read-only tools retry freely. `AgentJobWorkflow` resolves `package.tools` against the catalog and wraps each with `activity_as_tool`. An unknown tool name fails non-retryably rather than silently running a toolless agent. Adding a tool = a catalog entry + a manifest name, never a workflow edit.

**Mockoon:** the desktop app (9.6.1) is installed but not the CLI; `npx @mockoon/cli@9.8.0` runs it with no global install, and `make mockoon` uses that. `mocks/payment-service.json` uses a CRUD route over a data bucket so POSTs accumulate and `GET /payments` lists them. A `{{{ len (data 'payments') }}}` count endpoint did **not** work (the helper resolved to 0 while the bucket had entries), so the count is taken as the length of `GET /payments` instead — fewer moving parts.

Verified: `make verify-payment` (dedupe across retries, no Temporal needed), `make verify-tool-call` (real workflow → Claude Sonnet 5 chooses the tool → `issue_payment` appears in the history as a scheduled activity → provider count +1 exactly), `make replay` clean over 3 real histories.

## 2026-08-21 — E1.3: approval policy is declarative, and two findings from running it

**Manifest shape.** Approval is a structured policy, not an expression string:

```yaml
approval_policy:
  tool: issue_payment
  field: amount_usd
  operator: gt
  value: 200
```

The Notion sketch used `when: "refund_usd > 200"`, which reads better but needs a sandboxed expression evaluator running *inside workflow code* — an `eval` surface and a determinism risk in the one place we can least afford either. Declarative fields are safe, statically validatable by `dos agent validate`, and can gain an expression-sugar layer later that compiles down to this.

**How the interrupt actually works** (worth knowing before editing `app/workflows/approval.py`): `event.interrupt(...)` does not return on the first pass — it suspends the agent and `invoke_async` returns `AgentResult(stop_reason="interrupt", interrupts=[...])`. When the workflow resumes the agent with an `interruptResponse`, the hook runs *again* and `event.interrupt(...)` now returns the human's decision. The same code path both asks the question and consumes the answer. The workflow side is a `while` loop, not an `if`, because an agent can pause more than once per session; `wait_condition` holds no worker resources, so a session can sit pending a human indefinitely at no cost.

Two findings from running it for real:

1. **`activity_as_tool` nests arguments under the parameter name.** An activity with signature `issue_payment(request: PaymentRequest)` produces tool input `{"request": {"amount_usd": 900, ...}}`, not `{"amount_usd": 900}`. The first run therefore paid without pausing — the policy looked for a top-level `amount_usd` and found nothing. An agent author must not have to know the Python signature wraps args in `request`, so `resolve_field()` searches a bare field name at any depth (keys walked in sorted order, so the result is stable across replay) and honours an explicit dotted path (`request.amount_usd`) for disambiguation.

2. **`make replay` caught a genuine non-determinism, and the root cause was my own process error.** After fixing (1), replaying the pre-fix history failed with `No command scheduled for event ActivityTaskScheduled` — the old history says "call the tool", the new code says "pause for approval". That is a real behaviour change, and I had made it **while reusing `BUILD_ID=v1`**, so old and new code shared one version identity. That is exactly the mistake Worker Versioning exists to prevent and proof 2 exists to demonstrate.

   Two consequences, both now in place: `BUILD_ID` moved to `.env` and must be **bumped whenever workflow behaviour changes**; and `scripts/replay.py` is scoped to the *current* build id, because `AgentJobWorkflow` is PINNED — a run started on v1 is only ever replayed by v1 code, so replaying older builds' histories would fail on every intentional change while proving nothing about what can happen in production. The scoping is not a way to silence the guard: reusing a build id across a behaviour change still produces exactly this failure, which is the correct outcome.

Verified on `agent-control-plane:v2`: `make verify-interrupt` — approve → payment reaches the provider (+1); deny → payment does not (+0) and the agent explains it was blocked by a reviewer; the query reports what the session is waiting on; both sessions complete in a single run (resume, not restart). `make replay` clean.

## 2026-08-21 — E2.1/E2.2: agent packages, and a trailing space in the repo directory name

**`agents/` is authoring-time, DynamoDB is runtime.** `dos agent publish` reads `manifest.yaml` + `procedure.sop.md` from disk and writes one `AgentPackage` row; from then on the workflow only reads the registry (via the E1.1 activity). Consequences: publishing needs no redeploy and no worker restart, and the Lambda worker does not need `agents/` in its deployment bundle.

**SOP substitution happens per job, at runtime.** `render_sop()` is pure string work, so it is replay-safe inside workflow code. Manifest `parameters:` supply defaults; runtime context (`tenant_id`, `agent_id`, `agent_version`, `job_id`) overrides them. One published row therefore serves every tenant with tenant-aware prompts — the alternative (substituting at publish time) would need a separate published package per tenant. An unknown placeholder is left verbatim rather than blanked, so a typo shows up in the prompt instead of silently deleting an instruction.

**Approval policy is declarative**, per the earlier E1.3 entry — the manifest carries `tool`/`field`/`operator`/`value`, never an expression string.

**Validation is worth having.** `dos agent validate` catches, verified by deliberately breaking a manifest: an unregistered model, a tool not in the catalog, an `approval_policy` targeting a tool the agent does not declare, a `{{placeholder}}` not declared under `parameters:`, and a parameter declared but never used. All four classes reported at once rather than failing on the first.

**Typer for the CLI** — type-hint driven, good help/errors, pairs with the Pydantic models already in use. The CLI is a primary extensibility surface for anyone forking this, so the UX is worth a dependency.

### The repo directory name ends with a space

`/Users/darshitvora/Github/Temporal/Durable Agent Control Plane ` — note the trailing space. This **breaks editable installs**: `uv sync` writes the project path into `_editable_impl_*.pth`, and Python's `site` module strips trailing whitespace when processing `.pth` lines, so the path never resolves and the installed `dos` entrypoint fails with `ModuleNotFoundError: No module named 'cli'`.

Worked around, not fixed: the `Makefile` invokes the CLI as `uv run python -m cli.main` (which works because the working directory is on `sys.path`), and CLAUDE.md §10 documents `dos` accordingly. **Renaming the directory to drop the trailing space would fix it properly** and remove a whole class of latent shell-quoting and tooling hazards — flagged to the human, whose directory it is. Until then, anything relying on `.pth`-based path injection will silently not work.

## 2026-08-21 — Repo directory renamed; the `python -m cli.main` workaround is retired

The directory was renamed to `/Users/darshitvora/Github/Temporal/durable-agent-control-plane` — lowercase, hyphenated, no trailing space. This resolves the editable-install failure recorded in the entry above: `_editable_impl_durable_agent_control_plane.pth` now holds a path `site` does not mangle, and `uv run dos --help` works.

**Two pieces of rename fallout, worth knowing for anyone forking or moving this repo:** a venv is not path-portable. `uv sync` alone only rebuilt the project's own editable install — every dependency's console script in `.venv/bin` kept a shebang pointing at the old absolute path, so `make typecheck` died with `mypy: ... /Durable Agent Control Plane /.venv/bin/python3: No such file or directory` while `make lint` passed (ruff ships as a native binary with no shebang, so it was unaffected — a misleadingly partial green). `uv sync --reinstall` rewrote the scripts and `make ci` is clean again. The rule: after moving the repo, run `uv sync --reinstall`, not `uv sync`.

`DOS` in the `Makefile` is now `uv run dos`, and CLAUDE.md §10 plus the E2.2 backlog note are updated. The workaround entry above stays as written — it is the record of why the rename mattered.

## 2026-08-21 — E3.1: tenant Priority resolver, and T3 deferred

**T1 was already done before this story started.** The `Tenant` model and its DynamoDB CRUD (`app/registry/models.py`, `repository.py`) existed from E0.2 and already passed `make verify`. E3.1's real work was T2: nothing read a tenant's registry row before starting a workflow — `scripts/verify_agent_job.py` and `dos agent test` both hardcoded `Priority(...)` literals.

**`resolve_priority()` lives in `app/registry/priority.py`, not `repository.py`.** It's a client-side-only lookup-or-raise (tenant → real `Priority`), never called from workflow code. Keeping it out of `repository.py` (pure storage) means it's one shared implementation for every future workflow-starting call site — `dos agent test` today, the API in E4 and `dos demo flood` in E3.2 later — instead of each one re-deriving `Priority` from a `Tenant` by hand. An unknown tenant raises rather than defaulting, so a typo'd `--tenant` fails loudly instead of silently running at `priority_key=3, fairness_weight=1.0`.

**Verifying it required reading back the real Priority from Temporal Cloud, not just checking the SDK call didn't error.** `temporalio.api.workflow.v1.message_pb2.WorkflowExecutionInfo` carries a `priority` field (a `temporalio.api.common.v1.message_pb2.Priority` with `priority_key`/`fairness_key`/`fairness_weight`), reachable via `handle.describe()`'s `raw_description.workflow_execution_info.priority` — confirmed by inspecting the installed proto, not guessed. `scripts/verify_tenant_priority.py` asserts against it directly: two tenants with different registry values produce two different real payloads on Temporal Cloud.

**Three demo tenants seeded via the new `dos tenant add` CLI** (mirroring `dos agent`): `acme` (platinum, weight 3.0), `globex` (standard, weight 2.0), `initech` (free, weight 1.0) — all at the default `priority_key=3`, deliberately equal, so Proof 1 (E3.2) demonstrates fairness *weight* doing the protecting, not priority tier pre-sorting tenants into separate sub-queues. Initech is the flood generator per the E2.3 agent assignments; Acme and Globex are the tenants expected to hold steady. This is ordinary application-data writes into an already-provisioned table (same class of action as `dos agent publish`), not infrastructure provisioning — no `docs/AWS_SETUP.md` entry needed.

**T3 (per-tenant tool authorization via AgentCore Identity; denial visible in the session pane) is deferred out of E3.1**, confirmed with the human at the Inception gate. Building the AgentCore Identity integration now would have nowhere to surface its result — the session pane is an E5 UI component that doesn't exist yet — so it's left unchecked in `docs/BACKLOG.md` pointing at E5/E7 instead of built headless.

**Bedrock activity calls need `AWS_PROFILE` exported in the worker's actual OS environment, not just `.env`.** `pydantic-settings`' `env_file=".env"` only populates `Settings` fields; it does not export values into `os.environ`. `repository.py`'s DynamoDB calls work regardless because they explicitly thread `settings.aws_profile` into `boto3.Session(profile_name=...)`, but `temporal_client.py`'s `BedrockModel(model_id=..., region_name=...)` takes no profile and falls back to boto3's default credential chain — which only sees `AWS_PROFILE` if it's a real exported env var. Hit this restarting the worker for this story's verification (`NoCredentialsError` inside the model-invocation activity); fixed by exporting `AWS_PROFILE` before `uv run python -m app.worker`, not a code change. Worth knowing for anyone starting the worker in a fresh shell.

## 2026-08-21 — E3.2: fairness proof instrumentation

**Job lifecycle stays minimal — only `created_at` and `started_at`.** The wait metric needs exactly those two timestamps; no `completed_at`/status-on-finish was added, since nothing in this story needs it (that's E4.1's job-index territory). `dos demo flood` is the only caller that creates a `Job` row today; `dos agent test` and the verify scripts still don't, deliberately — see the no-op design below.

**`mark_job_started` is a no-op, not an error, when no Job row exists.** It's called unconditionally at the top of every `AgentJobWorkflow.run`, but only flood-submitted jobs have a Job row to update. The DynamoDB `UpdateItem` carries `ConditionExpression="attribute_exists(pk)"`, and a `ConditionalCheckFailedException` is swallowed in `repository.py`. This keeps the workflow generic for every caller — `dos agent test` and the verify scripts needed zero changes — rather than forcing every workflow-starting call site to adopt Job-row bookkeeping just because one new caller wants wait metrics. The activity call itself is wrapped in try/except inside the workflow, so even a genuine DynamoDB failure here can't fail the actual agent job — a metrics stamp is not allowed to matter more than the work it's measuring.

**This is a workflow behaviour change → `BUILD_ID` v3→v4.** `AgentJobWorkflow.run` now calls one more activity before doing anything else. Bumped per CLAUDE.md's versioning rule, worker deployment version set to v4 via `temporal worker deployment set-current-version`, verified against 20 real v4 histories with `make replay`.

**Real bug caught during verification: the new activity wasn't registered on the worker.** `app/worker.py`'s `activities=[...]` list named `resolve_agent_package` explicitly but not the new `mark_job_started` — both live in `app/activities/registry.py`. Every job still completed successfully (the workflow's try/except swallowed the resulting `NotFoundError`), but no wait data was ever recorded until the worker's activity list was fixed. Worth remembering: adding an activity to a module doesn't register it — it has to be added to the worker's explicit list too, and a workflow that silently tolerates an activity failure will hide a worker-side registration bug rather than surface it loudly.

**Fairness "off" is a client-side decision, not a Temporal Cloud setting.** Fairness engages the moment any job attaches a `fairness_key` — there's no cluster-level disable. A persisted `FairnessSetting` DynamoDB singleton (`pk=sk="SETTINGS#fairness"`, default `enabled=True`) is checked inside `resolve_priority()`: off means the `Priority` built has no `fairness_key`/`fairness_weight` at all, so the job falls back to the implicit shared empty-string key (plain FIFO within its priority tier) per the Priority & Fairness reference. Centralizing the check in the one shared resolver means `dos demo flood`, `dos agent test`, and any future caller all respect the toggle automatically.

**`dos demo flood` submits concurrently, not sequentially.** Awaiting each job's full completion before starting the next would never build a real queue backlog — the whole point of a flood is that `start_workflow` calls return once accepted, while actual processing is rate-limited by worker capacity and (with fairness on) round-robin dispatch. `asyncio.gather` over `count` un-awaited-until-the-end submissions is what actually exercises the fairness mechanism.

**The flood load-generator (`flood-load-agent`) is seeded in Python, not an `agents/` package.** Same precedent as `scripts/verify_agent_job.py`'s `verify-echo-agent` — a trivial, no-tools agent that exists purely to generate real queue load, which nobody hand-authors or iterates on via SOP edits. A real `agents/` folder would be ceremony with no author behind it.

**Verified at reduced scale, not the full 200-job demo count.** Every flood job runs the real `AgentJobWorkflow` → a real Bedrock call; there's no way to exercise the queue mechanics without also paying for the model calls. Confirmed the mechanism and the qualitative direction (fairness-on separates protected tenants from the flooder's wait; fairness-off collapses them together) with a 20+4+4 job concurrent flood against real AWS/Temporal Cloud — see `docs/BACKLOG.md` for the numbers. A full 200-job dress rehearsal (E3.2 T4, and again for E8's rehearsal pass) is deliberately left for a dedicated, cost-aware session rather than run repeatedly as routine verification.

## 2026-08-23 — E2.2 T5 and E4.1: install/uninstall, FastAPI, and the SSE bus ported from `durable-agentic-harness` (Superseded same day; see the Workflow Streams correction below)

**Keep new code minimal; reach for Temporal's own capabilities or an existing pattern before adding a new DynamoDB surface.** Standing guidance from the human during this story — applies beyond just this story. The concrete effect here: `GET /api/agents` uses one small scan (`list_agent_packages()`, mirroring `list_tenants()`'s existing scan-with-filter-expression exactly) rather than a richer query; `GET /api/jobs` reuses the existing tenant-scoped gsi1 query outright, no new repository code.

**`install_agent`/`uninstall_agent` validate both sides and raise, matching `resolve_priority`'s ethos.** An install for an unknown tenant or an unpublished agent fails loudly (`ValueError` → CLI `_fail` / API 404) rather than silently writing a broken reference. Idempotent — installing an already-installed agent is a no-op, not an error.

**`GET /api/agents` reads the registry, not disk — on purpose, not an oversight.** The CLI's `discover()`/`load_manifest()` (disk-based) is right for the CLI, which runs at authoring time with the source tree present. The API is meant to run as a deployed service (App Runner), and per E2.1's design the Lambda worker's bundle deliberately excludes `agents/` — publishing already writes everything the runtime needs into DynamoDB. An API route reading disk would work locally and silently return nothing once deployed. `list_agent_packages()` groups to the latest version per `agent_id` in the route handler, not the repository.

**The SSE bus is a near-verbatim port of `durable-agentic-harness/backend/fastapi_app/events.py`**, not a redesign — same `EventBus` (asyncio.Queue pub/sub keyed by job_id, here `job_id` instead of `workflow_id` — the same value in this codebase, since `start_workflow`'s `id=job.job_id`), same reasoning for the internal HTTP hop: the worker and the API are separate processes and can't share the in-memory bus, so a `notify_ui` activity (`app/activities/events.py`) POSTs to a token-protected `/internal/events` endpoint, which is the only thing that touches `bus.publish()` from outside the SSE route itself.

**`notify_ui` doesn't live in `app/activities/registry.py`.** That module's stated scope is "all DynamoDB I/O for the workflow" — an HTTP call to the API doesn't belong there. New `app/activities/events.py` instead, mirroring the reference project's `worker/activities/ui.py` naming.

**Event content is deliberately thin: job started, approval pending, approval resumed, job finished.** Confirmed with the human before building — E4.2 (Token streaming) is the backlog story for per-token/tool-call/guardrail event detail; pulling that forward here would blur the two stories. `UIEvent` (workflow-built, no timestamp — kept deterministic) gets its `ts` stamped by the activity on the way out, same pattern as `mark_job_started`'s `started_at`. Every `notify_ui` call is wrapped in the same non-fatal try/except as `mark_job_started` — a UI push failing must never fail the job it describes. Added to `worker.py`'s explicit activity list this time without incident (the exact bug from E3.2).

**Another workflow behaviour change → `BUILD_ID` v4→v5.** Verified against 3 real v5 histories with `make replay`, plus a live end-to-end run: subscribed to a job's SSE stream *before* starting it, watched `job_started` then `job_finished` arrive in order with the right payload, and confirmed a wrong `X-Internal-Token` gets a real 401.

**New dependencies: `fastapi`, `uvicorn[standard]`, `sse-starlette`.** The first API code in the repo; `uvicorn[standard]` (not bare `uvicorn`) because `--reload` needs `watchfiles`, already in the Makefile's `api` target.

**New config: `internal_api_base_url` / `internal_api_token`.** Separate from `api_host`/`api_port` — those are the bind address, not what a remote caller (the worker, possibly on Lambda) uses to reach the service; in cloud that's the App Runner service's internal URL, not `localhost`. Token generated for local `.env` (gitignored, never committed).

**`app/demo.py` extracted from `cli/main.py`.** `dos demo flood`'s concurrent-submission logic now has exactly one implementation, called by both the CLI and `POST /api/demo/flood`, instead of two copies drifting apart. `ramp`/`kill` demo-control endpoints are not stubbed — those CLI commands don't exist yet (E6, unbuilt), same precedent as E3.1's T3 deferral: only wrap what's real.

## 2026-08-23 — E4.1 T2 corrected: `temporalio.contrib.workflow_streams` replaces the hand-rolled event bus

**The custom `EventBus`/`/internal/events`/`notify_ui` design above was wrong, not just non-preferred.** The user pointed at `github.com/temporalio/samples-python`'s `workflow_streams` sample directory and asked for the codebase to be checked against it going forward. That sample ships `temporalio.contrib.workflow_streams` (Experimental as of `temporalio` 1.31.0, confirmed installed and matching the sample's API by introspecting the installed package directly rather than trusting the sample's version) — a durable, offset-addressed event channel the workflow itself hosts. The custom version built earlier the same day was strictly worse on every axis that mattered: the in-process `EventBus` dies with the API process (not durable), had no offset/resume for a reconnecting subscriber, and needed a hand-rolled shared-secret HTTP hop the native feature doesn't require at all. Fully replaced, not layered on top.

**`AgentJobWorkflow.__init__` is now `@workflow.init`-decorated, taking the same `AgentJobInput` as `@workflow.run`.** Required by the SDK for this pattern (its parameters must match `@workflow.run`'s exactly) and by the feature itself: constructing `self.stream = WorkflowStream()` here — not lazily inside `run()` — registers the stream's query/update handlers before the workflow accepts any external message, which is what lets `WorkflowStreamClient.create(client, job_id).subscribe(...)` attach reliably from outside. Verified against `workflow_streams/workflows/order_workflow.py` and `llm_workflow.py`, both of which construct their stream the same way for the same stated reason.

**All four of this workflow's events publish directly from workflow code — `self.events.publish(UIEvent(...))`, synchronous, no activity.** Every one of job_started / approval_pending / approval_resumed / job_finished is something the workflow itself already knows and decides; none of it originates inside an activity. That's different from the sample's `llm_activity.py`/`payment_activity.py`, which publish from *inside* an activity via `WorkflowStreamClient.from_within_activity()` because their content (LLM token deltas, payment progress) is genuinely activity-side. E4.2's token-level streaming will need that activity-side form, publishing onto this same stream from inside the Bedrock model-call activity — this workflow's stream and topic are already in place for it.

**Publishing from workflow code needs no try/except.** The earlier `notify_ui` design wrapped every call because an HTTP POST can fail; `WorkflowTopicHandle.publish()` is a synchronous, in-memory, deterministic append — it cannot fail the way network I/O can, so none of the "best-effort, log and continue" ceremony from the superseded design carried over. `mark_job_started` keeps its try/except (E3.2) because that one really is an activity calling out to DynamoDB.

**`await workflow.sleep(timedelta(milliseconds=500))` before returning, after the final `job_finished` publish.** Directly from the samples' documented reasoning (`order_workflow.py`, `llm_workflow.py`): without it, a subscriber's next poll can miss the terminal event because the workflow has already closed and the in-memory log is gone with it.

**The API needs a real, long-lived `Client` now, not none.** `app/api/main.py` gained a `lifespan` that connects once via the existing `app.temporal_client.connect()` (same `StrandsPlugin`-attached factory the worker uses) and stores it on `app.state`; the SSE route reads it from there. `WorkflowStreamClient.create()` needs a real client — there was nothing for the events route to hold onto before, since the old design never talked to Temporal from the API at all.

**Deleted:** `app/api/events.py` (the custom bus), `app/api/routes/internal.py` (the internal endpoint), `app/activities/events.py` (the `notify_ui` activity), and the `internal_api_base_url`/`internal_api_token` settings (`app/config.py`, `.env`, `sample.env`) — nothing in the corrected design needs a shared secret or a second HTTP surface between the worker and the API.

**Workflow behaviour change → `BUILD_ID` v5→v6.** Verified against 5 real v6 histories with `make replay` (including the payment, tool-call, and approve/deny interrupt flows — the approval loop is the code path this change touched most directly, so all three proofs were re-run, not just the new streaming path, per CLAUDE.md's definition of done). Live end-to-end: started a real job, subscribed via `WorkflowStreamClient` immediately after `start_workflow` returned (subscribing before the workflow exists correctly raises `RPCError: workflow not found` — expected Temporal semantics, not a bug, and not a real-world race since a client only ever has a `job_id` to subscribe with after the job that created it has already returned), watched `job_started` then `job_finished` arrive over the real SSE route in order.

**Lesson for future Temporal work in this repo, recorded as its own memory entry:** check `github.com/temporalio/samples-python` for the real pattern before building anything that looks like a Temporal-adjacent feature, rather than porting a pattern from a sibling project or designing from scratch.

## 2026-08-23 — E4.2: token streaming is one plugin argument, not a new activity

**`TemporalAgent(streaming_topic=...)` does the whole job.** The plugin ships both `invoke_model` and `invoke_model_streaming`; setting `streaming_topic` makes `TemporalModel.stream()` dispatch to the latter, which opens a `WorkflowStreamClient.from_within_activity()` and publishes every Strands `StreamEvent` onto the workflow's stream as it arrives (batched on `streaming_batch_interval`, default 100ms). Read from the installed plugin source (`_temporal_model.py`, `_model_activity.py`) and its README's Streaming section before building anything — which is what stopped this from becoming a hand-written streaming activity duplicating what the plugin already does. Exactly the lesson from the E4.1 correction, applied first this time.

**Two topics on one stream, not two streams.** `job_events` carries our `UIEvent`s published from workflow code; `model_stream` carries raw Strands `StreamEvent`s published from inside the model activity. Different payload types, same `WorkflowStream` — the heterogeneous-topic pattern the `workflow_streams` samples demonstrate, and the SSE route dispatches on `item.topic`.

**Raw `StreamEvent`s are translated in the API, not the UI.** `translate_stream_event()` in `app/api/routes/events.py` maps them to `token` / `reasoning` / `tool_call` and drops the rest (messageStart/messageStop/metadata frames, and the partial tool-input JSON that streams as `contentBlockDelta.delta.toolUse`). Keeps Strands' wire format out of the UI contract, so "adding an agent needs zero UI code" survives a future model or SDK change. Field paths were verified by introspecting the installed `strands.types.streaming` TypedDicts, not inferred — notably `reasoningContent.text` for Claude's extended thinking, which is a *sibling* of `text`, so treating `delta.text` as "the tokens" would silently drop all reasoning output.

**Guardrail-verdict and memory-recall event shapes deferred, not stubbed.** E4.2 T2 named four shapes; two have no source in the codebase (Bedrock Guardrails is E7.1 T3, AgentCore Memory is E7.1 T2). Defining them speculatively would mean designing against output shapes nobody has seen yet, so they are cross-referenced onto their owning E7.1 tasks instead. Same precedent as E3.1's T3.

**`GET /api/jobs/{id}/state` is the polled fallback's server half.** Real `describe()` plus the workflow's `pending_approval` query — status, worker deployment version, pending approval. The query is only issued when the execution is actually RUNNING; a closed workflow has no worker to answer one, and reporting status without approval detail is the honest answer rather than an error. The deployment version here doubles as proof 2's per-session version badge (E6.1 T3). The UI's switch-on-failure logic waits for E5.1 T4 — there is no UI to switch yet.

**A workflow with no worker assigned yet has no deployment version.** `verify_streaming.py` initially asserted `worker_version` immediately after `start_workflow` returned and failed with `None`. Not an endpoint bug: `versioning_info.deployment_version` is populated once a worker completes the first workflow task, and polling before that races it. The fix was in the test (wait for the first streamed event, which proves a worker has picked the job up), not the endpoint — `None` there is the correct answer for a not-yet-started workflow, and the UI badge should render it as such.

**Workflow behaviour change → `BUILD_ID` v6→v7.** Histories now schedule `invoke_model_streaming` instead of `invoke_model`. All three proofs re-verified on v7 rather than just the streaming path, since this changed the model call every job makes: payment idempotency, tool-call, and approve/deny interrupt (the last is also the acceptance criteria's "interrupt is possible mid-stream"). `make replay` clean against 5 v7 histories.

## 2026-08-23 — E2.3: two more reference agents, and one shared idempotency helper

**Incident Triage is tier 1 and recommends rather than acts.** Its stub README asked for tier 1 ("SOP only, no code") *and* "roll back a deployment", which cannot both be true. Resolved toward tier 1: the agent correlates an alert against recent deploys and recommends a rollback, escalation, or monitoring, naming the suspect deployment. That gives the repo a genuine no-code reference agent — the boilerplate story needs one — and, because it is also proof 1's flood generator, keeps 200 concurrent jobs free of tool calls and therefore fast and cheap.

**`dos demo flood` now floods with that real published agent; `flood-load-agent` is deleted.** E3.2 seeded a synthetic no-tools package in Python because no suitable real agent existed. Incident Triage is exactly that agent, and the stub always described it as the flood generator, so the synthetic one is gone. `submit_flood` resolves the agent's latest published version from the registry and raises a clear "not published" error rather than silently seeding one; `--agent` keeps it parameterisable.

**Dispute Resolution gets one tool per retry class.** `fetch_dispute_evidence` is read-only and is the catalog's first user of the `READ_ONLY` policy, which had been defined but unused since E1.2. `submit_dispute_response` is consequential: filing a chargeback rebuttal twice is the same class of bug as paying twice, so it runs through the same once-only mechanism as the payment activity.

**The once-only mechanism is now shared, extracted to `app/activities/idempotency.py`.** A second consequential tool would otherwise have duplicated ~15 lines of subtle proof-3 logic, which is a bad look in a repo meant to be forked as a reference. `run_once(prefix, perform)` owns the get-record / claim-key / perform / complete-record sequence and the key-stability comment; `payment.py` keeps only its HTTP call. This touches proof-3 code, so `make verify-payment` and `make verify-tool-call` were re-run specifically and both pass unchanged.

**An approval policy whose field the tool input never carries fails silently.** The Dispute Resolution manifest gates `submit_dispute_response` on `amount_usd`, but the first draft of `DisputeResponseRequest` had no such field — `resolve_field` would have returned `None` on every call and the gate would simply never have fired, with `dos agent validate` none the wiser (it checks the policy names a declared *tool*, not that the tool carries the *field*). Fixed by putting `amount_usd` on the filing, which is more faithful anyway. Worth knowing as a manifest-authoring trap, and a candidate validation rule if the tool schemas ever become introspectable.

**Mockoon's collection now serves more than payments**, so it is renamed "Demo Services" internally (the file stays `mocks/payment-service.json`; renaming it would churn the Makefile and two docs for no functional gain). Added `GET /disputes/:id/evidence` — a templated route whose reason code, amount, delivery status and prior-dispute count vary, so the agent has something real to reason over — and `POST /dispute-responses` as a CRUD route over its own data bucket, mirroring `/payments` so filings accumulate and can be counted. As with payments, the mock deliberately does **not** dedupe: the counter has to measure what actually reached the provider, not our own bookkeeping.

**VendorCheck (T4) deferred to E7.3.** Tier 3 hosted needs `invoke_hosted_agent` against AgentCore Runtime, plus a real deployed runtime and ARN. None exist, so it cannot meet E2.3's "executes a real job" bar; authoring a manifest against an unbuilt service contract would be guessing. Cross-referenced onto E7.3 T1, same precedent as E3.1 T3 and E4.2's guardrail/memory shapes.

**No `BUILD_ID` bump.** Adding tools to the catalog and refactoring an activity's internals changes worker registration and activity code, not workflow code — activity results come from history on replay, so determinism is unaffected. `make replay` clean against 18 v7 histories confirms it.

## 2026-08-24 — E5.1: the desktop shell, real fleet state, and a false alarm worth recording

**Running counts needed Temporal's visibility store, not another DynamoDB tally.** The process monitor's acceptance criteria ("no fake data") ruled out inventing a running count, and the DynamoDB job index (E3.2) can't supply a correct one — nothing stamps a job *finished*, so a naive count would only climb. `app/registry/fleet.py`'s `tenant_running()` uses `client.count_workflows(... GROUP BY ...)`/filtered counts against `WorkflowType = "AgentJobWorkflow"`, which is exactly what CLAUDE.md's own "what actually needs DynamoDB" reasoning would predict: this is live, derived, per-tenant state that Temporal already tracks, not admin-mutated config.

**That query needed a new `TenantId` custom search attribute on the Temporal Cloud namespace**, which the day-to-day API key cannot create (`tcld` confirmed this: `search-attribute list`/`add` both return `Request unauthorized` under the API key, `list-workflows`/`count-workflows` don't). Added via `tcld namespace search-attributes add --search-attribute "TenantId=Keyword"`, documented as a manual step in `docs/AWS_SETUP.md` per CLAUDE.md §2's "AWS actions are manual and documented" rule extended to Temporal Cloud namespace config — the same class of one-time, human-scoped action as creating the DynamoDB table. Every job start now carries it via `app/registry/priority.tenant_search_attributes()`, client-side only, no `BUILD_ID` bump. Until the attribute exists (or on a fresh namespace), `lanes()` reports `running: null` rather than `0` — confirmed both states live.

**Only what's real is on screen.** Status-strip tiles for sandbox count, S3 offload, and version ramp, and system controls for the ramp slider and kill-worker, are omitted entirely rather than shown disabled — their backends (E6, E7.2) don't exist. Confirmed with the human at Inception: an omitted tile costs nothing; a visibly-dead control on a conference screen costs credibility.

**Visual direction: industrial workstation chrome**, chosen over a phosphor-terminal or high-contrast-control-room direction at Inception. A real 3px hard bevel (two nested `box-shadow`s, not borders) on every panel and button is the signature — declared as Tailwind v4 `@utility` blocks (`raised`/`sunken`) rather than `@layer components`, since v4's `@apply` can't reference a class that isn't a real utility. Colour is reserved for state (`--color-signal` amber, `--color-alert` red); the chassis itself is grey.

**Stage legibility was measured, not eyeballed.** A first type-scale pass claimed "nothing below 15px" in a code comment while the actual rendered floor was 11px — caught by literally computing `getComputedStyle(...).fontSize` across every text node via Playwright, not by reading the CSS. Iterated until the true floor was 13px (secondary labels only) with a clean 13→30px ladder, and the claim in the comment now matches reality. `ui/e2e/shell.spec.ts` turns that measurement into a standing regression guard (`nothing on screen is smaller than the 13px stage floor`) rather than a one-time check — the exact failure mode (a comment asserting something no one measured) it exists to catch.

**Playwright E2E lives under `ui/e2e/`, not `scripts/e2e/`** as the 2026-08-21 decision (before E5 existed) assumed — it's an npm/Playwright-native project colocated with the frontend it drives, not a Python script; `make e2e` still gives it the promised Makefile entry point. All six specs run against the real API, real Temporal Cloud, and real Bedrock — no fixtures, no mocks, consistent with the rest of this repo's verification philosophy.

**A real debugging exercise that ended in a false alarm, worth recording so it isn't re-investigated:** the flood-and-stream E2E spec failed twice with the session terminal stuck on a stale, already-closed job, apparently never receiving live events. Chased through several wrong hypotheses in order — Vite's dev proxy buffering SSE (disproved: direct `curl` through the proxy streamed tokens live, headers showed `x-accel-buffering: no` and `Transfer-Encoding: chunked` correctly), a stale-job-selection bug in `list_jobs_for_tenant` (disproved: it's `ScanIndexForward=False`, correctly newest-first) — before instrumenting the actual browser's `EventSource` directly and finding the real cause: a manual diagnostic browser tab, left open from earlier debugging in this same session, was still polling all four fleet endpoints every 2 seconds and endlessly reconnecting a broken `EventSource` to an old completed job, in the background, for over 200 seconds, at the same time `npm run e2e` was running against the same local API. That contention was enough to make a real 2-second poll cycle take 79+ seconds to notice a new job. Closing that stale tab and rerunning the full suite passed cleanly and repeatably. No code changed as a result — the lesson is procedural: a leftover interactive debugging session against a shared local dev stack is a real confound for that stack's own automated tests, and the fix is closing it, not touching the code it was never actually implicating.
