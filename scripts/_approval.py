"""Await an agent job's result, approving any human-approval pause that fires.

For verify scripts whose agent *might* hit the approval gate depending on data
they don't control — Mockoon randomises the dispute amount 40-900 and the
gate trips above 500 — as opposed to `verify_interrupt.py`/`verify_pinning.py`,
which deliberately trigger a pause and poll *for* it.

The subtlety worth preserving: a single `pending_approval` query right after
`start_workflow` always returns None, because the run has not reached its
first tool call yet. Treating that None as "no approval will ever be needed"
and then awaiting `handle.result()` leaves the run paused forever when the
gate fires later — a hang that looks like a stuck worker or a network fault
(it eventually surfaces as a long-poll `RPCError: transport error`) but is
purely this race. So the poll has to run *alongside* the result, not before
it. See docs/DECISIONS.md.
"""

import asyncio
from typing import Any

from temporalio.service import RPCError

from app.workflows.agent_job import AgentJobWorkflow

POLL_INTERVAL_S = 2.0


async def result_with_auto_approval(handle: Any, verbose: bool = True) -> Any:
    """Return the job's outcome, approving approval pauses as they appear."""
    result_task = asyncio.create_task(handle.result())
    while True:
        done, _ = await asyncio.wait({result_task}, timeout=POLL_INTERVAL_S)
        if done:
            return await result_task
        try:
            pending = await handle.query(AgentJobWorkflow.pending_approval)
        except RPCError:
            # Racing a workflow that just closed: only a running workflow can
            # serve a query. The next loop returns the already-finished result.
            continue
        if pending is not None:
            if verbose:
                print(f"  auto-approving {pending.tool} (policy: {pending.policy})")
            await handle.signal(
                AgentJobWorkflow.submit_approval, args=[pending.interrupt_id, "approve"]
            )
