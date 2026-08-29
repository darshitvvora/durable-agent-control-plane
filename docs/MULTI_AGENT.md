# Multi-agent collaboration

Two agents can collaborate in two structurally different places, and the
difference matters more than it first looks. This document is the rule this
repo follows, and why.

> **The rule.** Collaboration *inside one job* is a Strands concern and runs
> inside a single Activity. Collaboration *between jobs* is a Temporal concern
> and runs as separate workflow executions. Never blur the two: the first is
> one durable unit of work, the second is many.

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
