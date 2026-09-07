"""Seed the demo's starting state (beat 0's memory moment).

Runs two real invoice-exception sessions so the tenant's AgentCore Memory
holds genuine, relevant prior decisions. Beat 0 then opens by recalling real
history rather than fabricated rows — CLAUDE.md §7 forbids fake data on
screen, and a real recall is a better moment anyway.

Run after `scripts/reset.py --yes`, never instead of it.

Waits via `result_with_auto_approval` rather than a bare `handle.result()`:
invoice-exception pauses for human approval above its 200 USD escalation
threshold, and the model's own risk-weighing (procedure.sop.md step 3) can
still choose to call `issue_payment` on an above-threshold invoice even when
the vendor's risk tier argues against it. A bare `.result()` would then hang
this script forever instead of seeding memory (see scripts/_approval.py's
docstring for the same race in the verify scripts).
"""

import asyncio

from app.sessions import start_session
from app.temporal_client import connect
from app.workflows.models import JobOutcome
from scripts._approval import result_with_auto_approval

TENANT = "acme"
AGENT = "invoice-exception"
PRIOR_SESSIONS = [
    "Invoice INV-6610 from Initech Supply for 780.00 USD failed purchase-order "
    "matching: quantity billed is 12, purchase order says 9. Decide and act.",
    "Invoice INV-6742 from Globex Retail for 95.00 USD failed purchase-order "
    "matching: unit price is 3.00 USD above the PO. Decide and act.",
]


async def main() -> int:
    client = await connect()
    for prompt in PRIOR_SESSIONS:
        job_id = await start_session(AGENT, TENANT, prompt)
        print(f"seeding {job_id}...")
        handle = client.get_workflow_handle(job_id, result_type=JobOutcome)
        outcome = await result_with_auto_approval(handle)
        print(f"  done: {outcome.stop_reason}")

    from app.activities.memory import recall_tenant_memory

    notes = await recall_tenant_memory(TENANT)
    print(f"\n{TENANT} now has {len(notes)} memory events:")
    for note in notes:
        print(f"  {note[:120]}")
    assert len(notes) >= 2, "seeding did not produce recallable memory"
    print("\nPASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
