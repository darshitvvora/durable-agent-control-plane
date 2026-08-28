"""Verify E6.2 — proof 3's live rehearsal: kill the worker at a tool boundary,
restart it, and confirm the session resumes at the same turn with the payment
counter reading exactly one extra call, not two.

Owns its own worker process (like `verify_pinning.py`) rather than requiring
`make worker` already running, so the kill is real: arm the kill switch,
submit a job whose payment call the current worker process will pick up, let
that process crash itself (`os._exit(1)` inside `run_once`, right after the
mocked payment call succeeds but before Temporal records completion), then
start a fresh worker process and confirm the SAME workflow finishes — Temporal
redelivers the pending activity task to whichever worker polls next.

Requires Mockoon running.
"""

import asyncio
import sys
import uuid

from app.config import get_settings
from app.demo import payment_count
from app.registry import repository as repo
from app.registry.models import KillSwitch
from app.registry.priority import resolve_priority
from app.temporal_client import connect
from app.workflows.agent_job import AgentJobWorkflow
from app.workflows.models import AgentJobInput

# Below the manifest's $200 escalation_threshold_usd, so this exercises the
# kill/resume mechanism on its own, not the approval gate (that's E1.3/E6.1).
PROMPT = "Invoice INV-KILL-1 for $60 to Globex Retail failed PO matching on quantity. Settle it."
WORKER_EXIT_TIMEOUT_S = 60
JOB_RESULT_TIMEOUT_S = 60


async def _spawn_worker():
    return await asyncio.create_subprocess_exec(
        sys.executable, "-m", "app.worker",
        stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.DEVNULL,
    )


async def main() -> None:
    settings = get_settings()
    print(f"build={settings.build_id} mockoon={settings.mockoon_base_url}")

    repo.put_kill_switch(KillSwitch(armed=False))  # clean slate
    baseline = await payment_count()
    print(f"payment count at start = {baseline}")

    worker = await _spawn_worker()
    client = await connect()
    try:
        print("arming the kill switch — next successful payment call crashes the worker")
        repo.put_kill_switch(KillSwitch(armed=True))

        job_id = f"verify-kill-{uuid.uuid4().hex[:8]}"
        handle = await client.start_workflow(
            AgentJobWorkflow.run,
            AgentJobInput(
                job_id=job_id, tenant_id="acme", agent_id="invoice-exception",
                agent_version=2, prompt=PROMPT,
            ),
            id=job_id, task_queue=settings.task_queue, priority=resolve_priority("acme"),
        )

        print(f"waiting up to {WORKER_EXIT_TIMEOUT_S}s for the worker to crash itself...")
        try:
            returncode = await asyncio.wait_for(worker.wait(), timeout=WORKER_EXIT_TIMEOUT_S)
        except TimeoutError:
            raise AssertionError(
                "worker did not exit — kill switch never fired (did the job reach issue_payment?)"
            ) from None
        assert returncode != 0, f"worker exited cleanly (code={returncode}), expected a crash"
        print(f"  worker process exited with code={returncode} — confirmed dead")

        assert not repo.get_kill_switch().armed, "kill switch should disarm itself when it fires"

        print("starting a fresh worker process to resume the pending session...")
        worker = await _spawn_worker()

        print(f"waiting up to {JOB_RESULT_TIMEOUT_S}s for the session to finish...")
        outcome = await asyncio.wait_for(handle.result(), timeout=JOB_RESULT_TIMEOUT_S)
        print(f"  finished: {outcome.output[:120]}")

        after = await payment_count()
        print(f"payment count after = {after}")
        assert after == baseline + 1, (
            f"expected exactly one new payment (baseline {baseline} -> {baseline + 1}), got {after}"
        )
        print("all checks passed — kill at the tool boundary, resume, exactly one payment")
    finally:
        worker.terminate()
        await worker.wait()


if __name__ == "__main__":
    asyncio.run(main())
