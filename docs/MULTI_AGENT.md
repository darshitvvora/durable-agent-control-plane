# Multi-agent collaboration

Two agents can collaborate in two structurally different places, and the
difference matters more than it first looks. This document is the rule this
repo follows, and why.

> **The rule.** Collaboration *inside one job* is a Strands concern and runs
> inside a single Activity. Collaboration *between jobs* is a Temporal concern
> and runs as separate workflow executions. Never blur the two: the first is
> one durable unit of work, the second is many.
>
> A third case sits outside both: an agent this control plane does not run at
> all, reached over a service boundary. That is the **hosted lane** (tier 3),
> and its durability is per-invocation — see the last section.

---

## Inside a job — Strands Swarm in one Activity

**Built. `app/activities/swarm.py`, exposed as the `investigate_fraud_swarm`
tool (E7.4 T1).**

Invoice Exception hands a flagged invoice to a two-agent Strands `Swarm`: an
Invoice Reviewer (entry point) that sizes up the case and hands off via
Strands' built-in `handoff_to_agent` tool, and a Fraud Specialist that returns
the verdict. The specialist carries a `structured_output_model`, so the tool
returns a typed `FraudSwarmVerdict` (`suspected_fraud`, `rationale`) rather
than prose the SOP would have to parse.

It is not invoked on every invoice. The SOP requires it only when vendor risk
is **not `low`** *and* the amount **exceeds the escalation threshold** — a
combination that genuinely warrants a second opinion, rather than a tool that
fires constantly to look impressive.

### Why the whole Swarm lives in one Activity

This is the part worth understanding before copying the pattern. A `Swarm`
**cannot** run in workflow code, for two independent reasons, both confirmed
by reading `strands/multiagent/swarm.py` rather than inferred:

1. **`Swarm.__init__` calls `run_async(...)` unconditionally** (to fire its
   `MultiAgentInitializedEvent` hook). That is the same thread-spawning sync
   path `temporalio.contrib.strands`'s own README warns about — "the
   synchronous form spawns a worker thread, which the workflow sandbox
   blocks." Merely *constructing* a Swarm inside a workflow deadlocks.
2. **Swarm's own bookkeeping reads `time.time()`** for `execution_timeout` /
   `node_timeout` / handoff limits. `strands` is a sandbox passthrough module
   here, so those calls are not replay-safe — a determinism hazard in exactly
   the place this project can least afford one.

There is also no `TemporalSwarm` analogous to `TemporalAgent`. The plugin's
README documents Models, Tools, Hooks, HITL, MCP, Streaming, Structured
Output and Continue-as-new — Swarm is simply not part of its surface.

So the Swarm runs **entirely inside one Activity**, built from plain
`strands.Agent` instances with a real `BedrockModel`. No `TemporalAgent`
inside: nothing within a single Activity needs its own per-call durability,
because the Activity *is* the unit Temporal retries.

### The tradeoff, stated plainly

| | Rest of this codebase | Inside the Swarm Activity |
|---|---|---|
| Unit of durability | one model call / one tool call | the whole multi-agent session |
| Worker dies mid-way | resumes at that call | re-runs the session from scratch |
| Visible in session terminal | every token, every tool call | one tool call |
| Determinism | enforced by the workflow sandbox | not applicable (Activity) |

That is a real downgrade in granularity, accepted deliberately and scoped to
one tool. It is why `investigate_fraud_swarm` gets its own retry class in
`app/activities/catalog.py` (`MULTI_AGENT`: a 180s timeout and only 2
attempts) — a retry re-runs two full agent turns, so retrying it five times
like a cheap lookup would be wasteful, and a 15s read-only timeout would kill
it mid-thought.

**If you need per-turn durability across collaborating agents**, don't use
`Swarm`. Orchestrate the handoff in workflow code with two `TemporalAgent`s
instead: every model call stays its own durable, resumable, streamed Activity.
That was the road not taken here (the backlog explicitly asked for Swarm), and
it remains the right choice for a long-running collaboration where losing
progress is expensive.

---

## Between jobs — separate workflows

Collaboration *between* jobs is not a Strands concern at all, and needs no
framework feature. Each job is already its own `AgentJobWorkflow` execution:
independently durable, independently retryable, independently visible in the
process monitor, and independently fair-queued per tenant. Two agents
collaborating at this level are simply two workflow executions exchanging
results.

This is the side of the line to prefer whenever the collaborating parties are
independently operated, independently scaled, or independently failed — the
properties a workflow boundary gives you for free are exactly the ones a
single Activity gives up (see the table above).

---

## The hosted lane — someone else's agent entirely

**Built. `app/activities/hosted.py`, tier 3, exposed as the VendorCheck
reference agent (E7.3).**

There is a third case the rule above doesn't cover: an agent this control
plane does not run *at all*. A tier-3 agent lives on AgentCore Runtime, in its
own container, with its own loop, its own model, its own tools and its own
data. `AgentJobWorkflow` reaches it with a single `InvokeAgentRuntime` call
and receives one answer.

That makes it structurally the same shape as the Swarm case — one Activity,
one coarse retry unit — but for a different reason. The Swarm *could* have
been decomposed and wasn't; a hosted agent *cannot* be, because the turns
happen on the other side of a service boundary we don't control.

### Durability is per-invocation, not per-turn

This is the sentence worth remembering about the hosted lane:

> Temporal gives you durability **around** a hosted agent, not **inside** it.

Concretely, for a tier-3 agent:

| | Native (tiers 1–2) | Hosted (tier 3) |
|---|---|---|
| Retry unit | one model call / one tool call | the whole invocation |
| Worker dies mid-way | resumes at that call | re-invokes from the start |
| Its internal tool calls | our activities, in Event History | invisible to us |
| `ApprovalGate` | enforced | **not available** |
| `GuardrailGate` | enforced | **not available** |
| Session terminal | live tokens, every tool call | one call, then the answer |
| Token streaming | yes | no |

The approval and guardrail rows are the ones with teeth. A hosted agent can
call whatever tools it likes, including consequential ones, and this control
plane will never see them — so it cannot pause them for a human or screen
them. That is not a gap to be closed later; it is what "hosted" means.

`dos agent validate` enforces this rather than letting it surprise anyone: a
tier-3 manifest declaring `tools`, `mcp_servers`, an `approval_policy`, or an
`output_model` is **rejected**, because every one of those would silently do
nothing. An author who writes an `approval_policy` on a hosted agent and sees
it accepted would reasonably believe payments were gated when nothing was
gating them.

### What you still get

Everything outside the invocation is unchanged, and it is not nothing:
per-tenant fair queueing and priority, retries with backoff, timeouts, the
job's place in the process monitor, tenant-scoped memory recall and write-back
around the call, full Event History of the invocation and its result, and
resumption of the *job* if the worker dies. The hosted agent is an opaque step
inside an otherwise fully durable process.

### Installing one takes an ARN, not a repo

Because there is no agent loop to configure on this side, a hosted agent needs
no package on disk at all — `dos agent register-hosted --runtime-arn ...`
writes the registry row directly. That is the tier-3 install story: a tenant
adopts a third-party agent from the store with an ARN someone handed them, no
repo access and no deploy. `agents/vendorcheck/` exists as a worked example
of the same thing expressed as a package; both produce the same registry row.
