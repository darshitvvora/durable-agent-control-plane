# Adding an agent

An agent is a **directory**, not a code change. Adding one requires **zero
workflow code and zero UI code** — that is the point of the whole design. If you
find yourself editing `app/workflows/` to add an agent, something has gone wrong;
raise it rather than working around it.

## The three tiers

| Tier | Who | What you write |
|---|---|---|
| 1 | Analyst, no code | `manifest.yaml` + `procedure.sop.md` |
| 2 | Developer | Tier 1 + a custom activity tool in `app/activities/` |
| 3 | External team | Your own container on AgentCore Runtime, registered by ARN |

## Tier 1 — no code

```bash
make agent-init ID=returns-triage      # scaffolds agents/returns_triage/
# edit agents/returns_triage/manifest.yaml and procedure.sop.md
make agent-validate ID=returns-triage  # checks model, tools, placeholders, policy
make agent-publish ID=returns-triage   # writes the registry row — no redeploy
make agent-test ID=returns-triage PROMPT="..."   # runs one real job
```

A complete worked example of exactly this loop lives in
[`agents/returns_triage/`](agents/returns_triage/) — two files, no code, verified
end to end by `make verify-returns-triage`.

`agent-publish` only writes a DynamoDB row. The generic `AgentJobWorkflow` and
the tool activities already exist, so there is nothing to deploy and no worker to
restart. `agents/` is an authoring-time surface; at runtime the workflow reads the
registry.

### `manifest.yaml`

See `agents/_template/manifest.yaml` for the annotated version. The fields that
matter:

- **`model`** — a key in the worker's model registry (`bedrock-claude`,
  `bedrock-nova`), *not* a Bedrock model id. See `app/temporal_client.py`.
- **`tools`** — names resolved against `TOOL_CATALOG` in
  `app/activities/catalog.py`. An unknown name fails the job non-retryably rather
  than quietly running a toolless agent.
- **`parameters`** — defaults for the `{{placeholders}}` your SOP uses.
- **`approval_policy`** — pause for a human before a matching tool call.
  Declarative (`tool` / `field` / `operator` / `value`), not an expression string,
  so nothing is `eval`'d inside workflow code.

### `procedure.sop.md`

This becomes the system prompt. Write plain English with RFC 2119 keywords
(MUST / SHOULD / MAY) so instructions are unambiguous.

`{{placeholders}}` are substituted **per job**, so one published package serves
every tenant. Available with no declaration: `{{tenant_id}}`, `{{agent_id}}`,
`{{agent_version}}`, `{{job_id}}`. Anything else must be declared under
`parameters:`. `agent-validate` fails on a placeholder you forgot to declare, and
on a parameter you declared but never used.

## Tier 2 — adding a tool

1. Write an activity in `app/activities/`. It MUST be idempotent if it has an
   external side effect — Temporal retries the *function*, not the side effect.
   See `app/activities/payment.py` for how a key derived from
   `activity.info().activity_id` survives a worker crash.
2. Register it in `TOOL_CATALOG` (`app/activities/catalog.py`) with the retry
   options for its class: `CONSEQUENTIAL` (moves money — few attempts, must be
   idempotent) or `READ_ONLY` (safe to repeat).
3. Name it under `tools:` in your manifest.

Note that `activity_as_tool` derives the tool schema from your activity's
signature, so an activity taking a single Pydantic argument produces nested tool
input (`{"request": {...}}`). Approval policies resolve a bare field name at any
depth, so you do not normally need to care.

## What you inherit for free

An agent authored at tier 1, by someone who has never heard of Temporal, gets
fair scheduling across tenants, crash recovery mid-turn without duplicated side
effects, and safe code upgrades while its sessions are in flight. That is the
strongest form of the operating-system claim: the platform is proven by the
applications other people write for it.

## Before you open a PR

- [ ] `make agent-validate ID=<id>` passes
- [ ] `make agent-test ID=<id> PROMPT="..."` runs a real job to a sensible result
- [ ] `make ci` passes (lint + typecheck)
- [ ] `make replay` passes if you touched anything under `app/workflows/`
- [ ] `BUILD_ID` bumped if workflow behaviour changed (see CLAUDE.md §4)
- [ ] Any non-obvious choice appended to `docs/DECISIONS.md`
