# Returns Triage

The worked **tier-1** example: `manifest.yaml` and `procedure.sop.md` are the
whole agent. No workflow code, no UI code, no activity, no Python — it was
authored entirely through the documented loop:

```bash
make agent-init ID=returns-triage
# edit the two files
make agent-validate ID=returns-triage
make agent-publish ID=returns-triage
uv run dos tenant install globex returns-triage
make agent-test ID=returns-triage PROMPT="..."
```

## What it does

Triages a customer's return request against the tenant's returns policy and
recommends one of `refund`, `refuse`, `inspect`, or `review`. Having no tools,
it never acts: a human or a downstream system carries the recommendation out.
That is what tier 1 means.

The policy lives in two manifest parameters — `return_window_days` and
`auto_approve_limit_usd` — so the same published package serves every tenant,
and changing the policy is a manifest edit and a republish, not a deploy.

## What it inherits for free

Nothing in this folder mentions Temporal, yet every job gets fair scheduling
against other tenants, crash recovery mid-turn, tenant-scoped memory recall, a
live token stream to the session terminal, and safe version routing while
sessions are in flight.

## Verifying it

```bash
make verify-returns-triage
```

Six real jobs on Temporal Cloud against real Bedrock, one per SOP rule —
including the two cases where rules deliberately conflict (a faulty item above
the auto-approve limit must still refund; a used item below it must still go to
inspection) — plus an assertion that the history contains **no tool activities**,
which is the tier-1 property itself rather than a claim in the manifest.
